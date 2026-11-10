{
  description = "Entwicklungsumgebung für ODP-Vorlesekarten";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python3.withPackages (ps: [
            ps.reportlab
          ]);
        in {
          default = pkgs.mkShell {
            packages = [
              python
              pkgs.libreoffice
            ];

            shellHook = ''
              echo "ODP-Vorlesekarten-Umgebung bereit."
              echo "Python: $(python --version)"
              echo "Start: python vorlesekarten.py praesentation.odp"
            '';
          };
        });
    };
}
