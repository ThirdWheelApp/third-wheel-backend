# Third Wheel Backend - Nix Configuration for Replit
# Provides system dependencies for the application

{ pkgs }: {
  deps = [
    # Python 3.11
    pkgs.python311
    pkgs.python311Packages.pip

    # PostgreSQL client libraries
    pkgs.postgresql

    # Development tools
    pkgs.python311Packages.pylsp-mypy
    pkgs.python311Packages.python-lsp-server

    # For healthchecks and debugging
    pkgs.curl
    pkgs.jq
  ];

  env = {
    PYTHON_LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
      pkgs.stdenv.cc.cc.lib
      pkgs.zlib
      pkgs.glib
    ];
    PYTHONBIN = "${pkgs.python311}/bin/python3.11";
    LANG = "en_US.UTF-8";
  };
}
