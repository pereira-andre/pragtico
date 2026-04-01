from __future__ import annotations

import os
import sys
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from integrations.ais_service import create_ais_service


if __name__ == "__main__":
    service = create_ais_service(ROOT_DIR)
    embed = service.embed_context()
    print("Fonte: VesselFinder")
    print(f"Centro: {embed['latitude']}, {embed['longitude']} | zoom {embed['zoom']}")
    print("Abre o dashboard da aplicação para ver o mapa embebido.")
