{
  description = "fw2tar: unprivileged firmware image -> rootfs tarball converter";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    # The rehosting forks. Pinned by flake.lock to exact revisions.
    unblob.url = "github:rehosting/unblob/update-upstream-26.6.4";

    binwalk-v2 = {
      url = "github:rehosting/binwalk";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      unblob,
      binwalk-v2,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: import nixpkgs { inherit system; };
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          lib = pkgs.lib;

          # unblob fork (already a flake): the wrapped `unblob` application plus
          # the runtime extractor toolchain it carries.
          #
          # Skip unblob's own pytest checkPhase here: its integration fixtures
          # live in git-LFS (tests/integration, tests/files), and the `github:`
          # flake fetcher does not smudge LFS, so the fixtures arrive as pointer
          # files and the tests fail. unblob's suite is validated in its own CI /
          # a `nix build` from an LFS checkout; for the image we only need the
          # built package.
          unblobPkg = unblob.packages.${system}.default.overrideAttrs (_: {
            doCheck = false;
          });

          # binwalk v2 (2.3.3) still uses the `imp` module, removed in Python
          # 3.12, so it must run on <=3.11 (the Dockerfile used Ubuntu's 3.10).
          binwalkPython = pkgs.python311;

          # binwalk v2 (rehosting fork) as an importable module only.
          binwalk2 = binwalkPython.pkgs.callPackage ./nix/binwalk2.nix {
            src = binwalk-v2;
          };

          # binwalk v3 (Rust) owns the `binwalk` binary.
          binwalkV3 = pkgs.binwalk;

          # Python interpreter fw2tar uses for `python3 -m binwalk` (v2) and
          # `python3 -m stitch` (fwstitch). stitch is pure-stdlib.
          pythonEnv = binwalkPython.withPackages (ps: [ binwalk2 ]);

          fw2tar = pkgs.rustPlatform.buildRustPackage {
            pname = "fw2tar";
            version = (lib.importTOML ./Cargo.toml).package.version;

            src = lib.fileset.toSource {
              root = ./.;
              fileset = lib.fileset.unions [
                ./Cargo.toml
                ./Cargo.lock
                ./src
                ./benches
              ];
            };

            cargoLock = {
              lockFile = ./Cargo.lock;
              outputHashes = {
                # git dependency: github.com/jamcleod/tar-rs
                "tar-0.4.44" = "sha256-z5EnV9bsoa2kOvnGnyTRaygQiZJwaB2rn6CunIhJafU=";
              };
            };

            # fw2tar's own logic is exercised by `cargo test`; the benches need
            # criterion and are not part of the package check.
            doCheck = true;

            meta = {
              description = "Unprivileged firmware image -> rootfs tarball converter";
              homepage = "https://github.com/rehosting/fw2tar";
              license = lib.licenses.mit;
              mainProgram = "fw2tar";
            };
          };

          # Extraction tools that binwalk (v2/v3) shells out to. unblob already
          # wraps these into its own PATH, but binwalk inherits the image PATH,
          # so they must be present at the image level too. Reuse unblob's own
          # runtime set and add the binwalk-specific extras.
          extractionTools =
            unblobPkg.passthru.runtimeDeps
            ++ (with pkgs; [
              squashfsTools
              cramfsprogs
              p7zip
              sleuthkit
              mtdutils
              cabextract
              gzip
              bzip2
              xz
              zstd
              lz4
              lzop
              lziprecover
              gnutar
              cpio
              unzip
            ]);

          # Host-facing entry points (mirror the Dockerfile's
          # fakeroot_fw2tar / fwstitch). fw2tar runs its extractors under
          # fakeroot so firmware uid/gid/mode metadata is preserved unprivileged.
          fakerootFw2tar = pkgs.writeShellScriptBin "fakeroot_fw2tar" ''
            exec ${pkgs.fakeroot}/bin/fakeroot ${fw2tar}/bin/fw2tar "$@"
          '';

          fwstitch = pkgs.writeShellScriptBin "fwstitch" ''
            exec env PYTHONPATH="${./utils/stitch}''${PYTHONPATH:+:$PYTHONPATH}" \
              ${pythonEnv}/bin/python3 -m stitch "$@"
          '';

          dockerImage = pkgs.dockerTools.buildLayeredImage {
            name = "rehosting/fw2tar";
            tag = "latest";

            contents =
              [
                fw2tar
                fakerootFw2tar
                fwstitch
                unblobPkg
                binwalkV3
                pythonEnv
                pkgs.fakeroot
                pkgs.bashInteractive
                pkgs.coreutils
                # /etc/passwd + /etc/group with a root entry: binwalk v2 calls
                # pwd.getpwuid(os.getuid()), which fails on the otherwise
                # password-file-less minimal image. Extraction runs under
                # fakeroot (uid 0), so the root entry is what gets looked up.
                pkgs.dockerTools.fakeNss
              ]
              ++ extractionTools;

            # The minimal image ships no /tmp; fw2tar (and the extractors) need a
            # writable temp + HOME for scratch extraction and tool config.
            extraCommands = ''
              mkdir -p tmp && chmod 1777 tmp
              mkdir -p root && chmod 0777 root
            '';

            config = {
              Cmd = [ "${pkgs.bashInteractive}/bin/bash" ];
              Env = [
                "PATH=/bin"
                "HOME=/root"
                "TMPDIR=/tmp"
                "LC_ALL=C.UTF-8"
                "LANG=C.UTF-8"
                "FW2TAR_LOG=warn"
              ];
            };
          };
        in
        {
          inherit
            fw2tar
            binwalk2
            dockerImage
            ;
          default = fw2tar;
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.cargo
              pkgs.rustc
              pkgs.rust-analyzer
              pkgs.clippy
            ];
          };
        }
      );
    };
}
