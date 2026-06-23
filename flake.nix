{
  description = "fw2tar: unprivileged firmware image -> rootfs tarball converter";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    # The rehosting forks. Pinned by flake.lock to exact revisions.
    unblob.url = "github:rehosting/unblob";

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
          unblobPkg = unblob.packages.${system}.default.overridePythonAttrs (_: {
            doCheck = false;
            doInstallCheck = false;
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

          # Python interpreter for `python3 -m binwalk` (v2). binwalk v2 is the
          # binwalk2 module and is pinned to <=3.11 (it imports `imp`).
          pythonEnv = binwalkPython.withPackages (ps: [ binwalk2 ]);

          # fwstitch (the LLM-driven multi-fs stitcher) runs on its own env: it
          # needs openai/pydantic/pyyaml (utils/stitch/requirements.txt), which
          # don't build on 3.11 (a doc dep needs >=3.12), so use the default
          # python3 here rather than binwalk's pinned 3.11.
          stitchEnv = pkgs.python3.withPackages (ps: [
            ps.openai
            ps.pydantic
            ps.pyyaml
          ]);

          # The `stitch` package, scoped so PYTHONPATH=${stitchPath} makes
          # `python3 -m stitch` importable (the dir must *contain* `stitch/`).
          stitchPath = lib.fileset.toSource {
            root = ./utils;
            fileset = ./utils/stitch;
          };

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
            exec env PYTHONPATH="${stitchPath}''${PYTHONPATH:+:$PYTHONPATH}" \
              ${stitchEnv}/bin/python3 -m stitch "$@"
          '';

          # Container UX entry points, byte-for-byte from src/resources (mirrors
          # the Dockerfile). `banner.sh` is the default CMD and prints install
          # instructions; the *_install[.local] scripts emit installer shell
          # scripts the user pipes to `sh`/`sudo sh` on the host to drop the
          # `fw2tar`/`fwstitch` host wrappers onto their PATH. They read the
          # wrapper sources from /usr/local/src (created in extraCommands).
          containerTools = pkgs.runCommand "fw2tar-container-tools" { } ''
            mkdir -p $out/bin
            cp ${./src/resources/banner.sh}            $out/bin/banner.sh
            cp ${./src/resources/fw2tar_install}       $out/bin/fw2tar_install
            cp ${./src/resources/fw2tar_install.local} $out/bin/fw2tar_install.local
            cp ${./src/resources/fwstitch_install}       $out/bin/fwstitch_install
            cp ${./src/resources/fwstitch_install.local} $out/bin/fwstitch_install.local
            chmod +x $out/bin/*
          '';

          imageContents =
            [
              fw2tar
              fakerootFw2tar
              fwstitch
              containerTools
              unblobPkg
              binwalkV3
              pythonEnv
              pkgs.fakeroot
              pkgs.bashInteractive
              pkgs.coreutils
              pkgs.findutils
              pkgs.gnugrep
              # tput, used by banner.sh / the host wrappers when on a terminal.
              pkgs.ncurses
              # /etc/passwd + /etc/group with a root entry: binwalk v2 calls
              # pwd.getpwuid(os.getuid()), which fails on the otherwise
              # password-file-less minimal image. Extraction runs under
              # fakeroot (uid 0), so the root entry is what gets looked up.
              pkgs.dockerTools.fakeNss
            ]
            ++ extractionTools;

          # The minimal image ships no /tmp; fw2tar (and the extractors) need a
          # writable temp + HOME for scratch extraction and tool config.
          imageExtraCommands = ''
            mkdir -p tmp && chmod 1777 tmp
            mkdir -p root && chmod 0777 root
            # Many scripts use a `#!/usr/bin/env ...` shebang; the minimal image
            # only has /bin/env (from coreutils), so provide the usual path too.
            mkdir -p usr/bin && ln -sf /bin/env usr/bin/env
            # Host-wrapper sources the *_install scripts embed and copy out.
            # (Dockerfile: COPY ./fw2tar /usr/local/src/fw2tar_wrapper, etc.)
            mkdir -p usr/local/src
            cp ${./fw2tar}   usr/local/src/fw2tar_wrapper
            cp ${./fwstitch} usr/local/src/fwstitch_wrapper
            # Print the install banner on interactive shells (Dockerfile parity).
            mkdir -p etc
            printf '%s\n' '[ ! -z "$TERM" ] && [ -z "$NOBANNER" ] && banner.sh' >> etc/bash.bashrc
          '';

          imageConfig = {
            # Default command: print install instructions (Dockerfile parity).
            # `docker run -it <img> bash` still drops to a shell.
            Cmd = [ "/bin/banner.sh" ];
            Env = [
              "PATH=/bin"
              "HOME=/root"
              "TMPDIR=/tmp"
              "LC_ALL=C.UTF-8"
              "LANG=C.UTF-8"
              "FW2TAR_LOG=warn"
              "FW2TAR_LOG_STYLE=always"
            ];
          };

          dockerImage = pkgs.dockerTools.buildLayeredImage {
            name = "rehosting/fw2tar";
            tag = "latest";
            contents = imageContents;
            extraCommands = imageExtraCommands;
            config = imageConfig;
          };

          # Filesystem *builders* the behaviour harness uses to synthesise
          # fixtures (the apt equivalents the tests/behavior/Dockerfile installs).
          # The base image already carries the mkfs tools for
          # ext*/squashfs/cramfs/jffs2/ubifs via extractionTools.
          fixtureBuilders = with pkgs; [
            genromfs
            cdrkit # genisoimage
            mtools
            dosfstools
            ntfs3g
            zip
            util-linux # mkfs.cramfs (fixture builder; distinct from cramfsck)
            # (mkyaffs2image / yaffs2utils is not in nixpkgs; yaffs stays a
            # documented skip in the harness's EXPECT table, same as before.)
          ];

          # The fw2tar image plus fixture builders, so the behaviour harness can
          # run end-to-end in one Nix-built image (no apt layer needed).
          testImage = pkgs.dockerTools.buildLayeredImage {
            name = "rehosting/fw2tar-test";
            tag = "latest";
            contents = imageContents ++ fixtureBuilders;
            extraCommands = imageExtraCommands;
            config = imageConfig;
          };
        in
        {
          inherit
            fw2tar
            binwalk2
            dockerImage
            testImage
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
