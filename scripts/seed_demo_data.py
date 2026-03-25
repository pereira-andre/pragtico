#!/usr/bin/env python3
"""Seed demo data: 2 agents, 2 vessels with different manoeuvres.

Clears existing port calls and creates fresh demo data.
Run: python3 scripts/seed_demo_data.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv()

from storage import create_store


def main() -> None:
    store = create_store(
        data_dir=os.path.join(BASE_DIR, "data"),
        knowledge_dir=os.path.join(BASE_DIR, "knowledge"),
    )

    # --- Create users if they don't exist ---
    users = [
        ("admin@porto.pt", "123456", "admin", "Administrador", "APSS"),
        ("agente.sulnave@porto.pt", "123456", "agente", "João Silva", "Sulnave"),
        ("agente.belnave@porto.pt", "123456", "agente", "Maria Santos", "Belnave"),
        ("piloto.pereira@porto.pt", "123456", "piloto", "André Pereira", "Pilotagem Setúbal"),
    ]
    for email, pwd, role, name, org in users:
        try:
            existing = store.get_user_profile(email)
            if not existing:
                store.create_user(
                    username=email, password=pwd, role=role,
                    full_name=name, organization=org, email=email, phone="",
                )
                print(f"[seed] Criado: {email} ({role})")
            else:
                print(f"[seed] Já existe: {email}")
        except Exception as e:
            print(f"[seed] Erro {email}: {e}")

    # --- Clear existing port calls ---
    try:
        all_calls = store.list_port_calls()
        for pc in all_calls:
            try:
                store.delete_port_call(pc["id"])
            except Exception:
                pass
        print(f"[seed] Removidas {len(all_calls)} escalas existentes")
    except Exception as e:
        print(f"[seed] Erro ao limpar: {e}")

    # --- Create demo port calls ---
    tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)

    # Escala 1: Graneleiro da Sulnave — entrada pendente
    try:
        pc1 = store.create_port_call(
            vessel_name="Atlantic Bulker",
            vessel_imo="9723345",
            vessel_call_sign="CQAN7",
            vessel_flag="Madeira",
            vessel_type="Graneleiro (Granéis sólidos)",
            vessel_loa_m="189.90",
            vessel_beam_m="32.26",
            vessel_gt_t="32540",
            vessel_dwt_t="58000",
            vessel_max_draft_m="12.80",
            eta_local=f"{tomorrow.strftime('%Y-%m-%d')}T08:00",
            berth="SAPEC Sólidos",
            last_port="Sines",
            next_port="Vigo",
            maneuver_type="entry",
            created_by="agente.sulnave@porto.pt",
            draft_m="11.20",
            tug_count="2",
            notes="Granéis sólidos — carga de cimento",
        )
        print(f"[seed] Escala 1: {pc1['vessel_name']} (Sulnave) — entrada pendente {tomorrow.strftime('%d/%m')}")
    except Exception as e:
        print(f"[seed] Erro escala 1: {e}")

    # Escala 2: RoRo da Belnave — entrada pendente + saída planeada
    try:
        pc2 = store.create_port_call(
            vessel_name="Setubal Express",
            vessel_imo="9845123",
            vessel_call_sign="D5BX9",
            vessel_flag="Portugal",
            vessel_type="Roll-on Roll-off",
            vessel_loa_m="199.90",
            vessel_beam_m="26.50",
            vessel_gt_t="26800",
            vessel_dwt_t="12500",
            vessel_max_draft_m="7.20",
            eta_local=f"{tomorrow.strftime('%Y-%m-%d')}T14:00",
            berth="Autoeuropa",
            last_port="Emden",
            next_port="Tânger",
            maneuver_type="entry",
            created_by="agente.belnave@porto.pt",
            draft_m="6.80",
            tug_count="1",
            notes="RoRo — veículos Autoeuropa",
        )
        print(f"[seed] Escala 2: {pc2['vessel_name']} (Belnave) — entrada pendente {tomorrow.strftime('%d/%m')}")

        # Schedule departure for day after
        try:
            store.schedule_departure_plan(
                port_call_id=pc2["id"],
                planned_departure_at=f"{day_after.strftime('%Y-%m-%d')}T06:00",
                next_port="Tânger",
                updated_by="agente.belnave@porto.pt",
            )
            print(f"[seed] Escala 2: saída planeada para {day_after.strftime('%d/%m')} 06:00")
        except Exception as e:
            print(f"[seed] Erro planear saída: {e}")

    except Exception as e:
        print(f"[seed] Erro escala 2: {e}")

    # --- Archive: 1 example completed manoeuvre ---
    try:
        past = datetime.now() - timedelta(days=3)
        pc3 = store.create_port_call(
            vessel_name="Sado Mineral",
            vessel_imo="9567890",
            vessel_call_sign="CQSM1",
            vessel_flag="Portugal",
            vessel_type="Graneleiro (Granéis sólidos)",
            vessel_loa_m="169.00",
            vessel_beam_m="27.00",
            vessel_gt_t="18500",
            vessel_dwt_t="28000",
            vessel_max_draft_m="10.50",
            eta_local=f"{past.strftime('%Y-%m-%d')}T10:00",
            berth="TMS 1 – Cais 5",
            last_port="Huelva",
            next_port="Aveiro",
            maneuver_type="entry",
            created_by="agente.sulnave@porto.pt",
            draft_m="9.80",
            tug_count="2",
            notes="Exemplo de arquivo — manobra concluída",
        )
        # Approve
        store.approve_port_call(
            port_call_id=pc3["id"],
            decided_by="piloto.pereira@porto.pt",
            approval_note="OK",
        )
        # Mark arrived
        store.mark_port_call_arrived(
            port_call_id=pc3["id"],
            arrived_at=f"{past.strftime('%Y-%m-%d')}T10:30",
            updated_by="piloto.pereira@porto.pt",
            berth="TMS 1 – Cais 5",
        )
        # Attach pilot report
        store.attach_entry_report(
            port_call_id=pc3["id"],
            updated_by="piloto.pereira@porto.pt",
            maneuver_started_at=f"{past.strftime('%Y-%m-%d')}T10:00",
            maneuver_finished_at=f"{past.strftime('%Y-%m-%d')}T10:45",
            draft_m="9.80",
            notes="Manobra sem incidentes. Vento NW 12 kts.",
        )
        print(f"[seed] Arquivo: {pc3['vessel_name']} — manobra concluída com registo")
    except Exception as e:
        print(f"[seed] Erro arquivo: {e}")

    print("\n[seed] Dados de demonstração criados com sucesso!")
    print("  admin@porto.pt / 123456")
    print("  agente.sulnave@porto.pt / 123456")
    print("  agente.belnave@porto.pt / 123456")
    print("  piloto.pereira@porto.pt / 123456")


if __name__ == "__main__":
    main()
