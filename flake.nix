{
  description = "WinPodX — Windows app integration for Linux desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Single source of truth for the package version. Reading from
      # pyproject.toml at evaluation time keeps the Nix store path
      # (winpodx-<version>) in sync with the Python distribution version
      # automatically — no need to bump two files at release time.
      pyprojectVersion = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          inherit (pkgs) lib;
          python = pkgs.python3;

          # Runtime CLI tools winpodx shells out to. Podman is the default
          # backend so it ships in the wrapper PATH; docker/libvirt stay
          # opt-in via the host to keep the closure bounded.
          runtimeBins = [
            pkgs.freerdp
            pkgs.iproute2
            pkgs.libnotify
            pkgs.podman
            pkgs.podman-compose
          ];
        in
        {
          default = self.packages.${pkgs.stdenv.hostPlatform.system}.winpodx;

          winpodx = python.pkgs.buildPythonApplication {
            pname = "winpodx";
            version = pyprojectVersion;
            pyproject = true;

            src = lib.cleanSource ./.;

            build-system = [ python.pkgs.hatchling ];

            dependencies = with python.pkgs; [
              pyside6
              docker
              libvirt
            ];

            nativeCheckInputs = with python.pkgs; [
              pytestCheckHook
            ];

            preCheck = ''
              export WINPODX_BUNDLE_DIR=$PWD
            '';

            # scripts/, config/ and data/ live at the repo root rather than
            # inside the Python package; ship them under share/ and point the
            # runtime at it so bundle_dir() resolves correctly for the wheel.
            postInstall = ''
              mkdir -p $out/share/winpodx
              cp -r scripts config data $out/share/winpodx/
            '';

            makeWrapperArgs = [
              "--prefix"
              "PATH"
              ":"
              (lib.makeBinPath runtimeBins)
              "--set"
              "WINPODX_BUNDLE_DIR"
              "${placeholder "out"}/share/winpodx"
            ];

            pythonImportsCheck = [ "winpodx" ];

            meta = {
              description = "Windows app integration for the Linux desktop (FreeRDP RemoteApp + dockur/windows)";
              homepage = "https://github.com/kernalix7/winpodx";
              license = lib.licenses.mit;
              mainProgram = "winpodx";
              platforms = lib.platforms.linux;
            };
          };
        }
      );

      checks = forAllSystems (pkgs: {
        winpodx = self.packages.${pkgs.stdenv.hostPlatform.system}.winpodx;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ self.packages.${pkgs.stdenv.hostPlatform.system}.winpodx ];
          # Runtime tools the wrapper would normally inject; expose them in
          # the dev shell so `python -m winpodx` works against the source tree.
          packages = [
            pkgs.freerdp
            pkgs.iproute2
            pkgs.libnotify
            pkgs.podman
            pkgs.podman-compose
            pkgs.ruff
            pkgs.mypy
          ];
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
          '';
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
