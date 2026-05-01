{
  description = "winpodx — Windows app integration for Linux desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
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
            version = "0.3.0";
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

            disabledTests = [
              # Assume repo-root data/ + scripts/ next to the package; not true
              # for the installed wheel pytestCheckHook runs against.
              "test_bundled_data_path_source_layout"
              "test_apply_windows_runtime_fixes_returns_per_helper_status"
              # `exec -a ... sleep` breaks on nixpkgs' multi-call coreutils.
              "test_find_existing_session_rejects_non_freerdp_pid"
            ];

            makeWrapperArgs = [
              "--prefix"
              "PATH"
              ":"
              (lib.makeBinPath runtimeBins)
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
        winpodx = self.packages.${pkgs.system}.winpodx;
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
