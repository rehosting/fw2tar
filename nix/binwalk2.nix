# binwalk v2 (rehosting fork) packaged as an importable Python *library*.
#
# fw2tar invokes the legacy binwalk through `python3 -m binwalk` (see
# src/extractors/binwalk.rs), while the modern binwalk v3 (Rust, from nixpkgs)
# owns the `binwalk` binary and is invoked directly. To let both coexist in one
# image we keep only the importable module here and drop the v2 `binwalk`
# console script, so it cannot collide with the v3 binary.
{
  lib,
  buildPythonPackage,
  setuptools,
  src,
}:

buildPythonPackage {
  pname = "binwalk";
  version = "2.3.3";
  pyproject = true;

  inherit src;

  build-system = [ setuptools ];

  # binwalk delegates extraction to external CLI tools (7z, sasquatch, cramfsck,
  # …) which the image provides on PATH; it has no hard Python dependencies.
  dependencies = [ ];

  # The test suite shells out to extractors and network fixtures; the behaviour
  # we care about is covered by fw2tar's in-image behaviour harness instead.
  doCheck = false;

  pythonImportsCheck = [ "binwalk" ];

  # Drop the v2 entry-point script; v3 (nixpkgs) owns the `binwalk` binary.
  postInstall = ''
    rm -f "$out/bin/binwalk"
  '';

  meta = {
    description = "binwalk v2 (rehosting fork) as a python3 -m binwalk module";
    homepage = "https://github.com/rehosting/binwalk";
    license = lib.licenses.mit;
  };
}
