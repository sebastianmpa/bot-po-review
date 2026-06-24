"""
Script de prueba: consulta crossovers y packs en PostgreSQL
para los part numbers del PO 93433 (Husqvarna).

Flujo:
  1. Resuelve MFRID desde MySQL ERP (prontoweb.product)
  2. Consulta product_crossover en PostgreSQL
  3. Consulta product_packs en PostgreSQL
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from utils.bd_mysql import get_db_connection, get_pg_connection

# ── Part numbers del PO 93433 (del log) ──────────────────────────────────────
PART_NUMBERS = [
    '501051401', '501269705', '501742501', '503798802', '503816001',
    '503887901', '504597008', '505155801', '510019801', '510244105',
    '512846401', '523056801', '525435601', '525865401', '529626301',
    '530014347', '531008655', '531314801', '532105709', '532109808',
    '532187690', '532199918', '532408047', '532425923', '535280001',
    '535402832', '536567901', '537024003', '537076801', '537210305',
    '537230502', '537264903', '537291702', '537337201', '537424507',
    '537538301', '537617401', '539101977', '539105521', '541378001',
    '544080803', '544380201', '545033501', '545081813', '545081848',
    '545084901', '545180847', '548102301', '548866401', '574173002',
    '575532001', '575635502', '577094601', '577167201', '577271501',
    '577885601', '578931504', '580727101', '580921101', '581023001',
    '581047401', '581146502', '581332806', '581506903', '582062502',
    '582541101', '583207101', '583487601', '584299801', '585605001',
    '586001103', '586351004', '586429702', '586836702', '587070202',
    '587241401', '587241501', '587358401', '587599801', '541258101',
    '588078303', '588117901', '588813801', '589464805', '589464902',
    '590940201', '591375401', '595303401', '596248601', '596433701',
    '596565301', '596762301', '596989801', '597024402', '598453201',
    '598684407', '599158101', '599334601', '596203801',
    '525814801', '544097903', '582973101',  # PART_ERROR
]


# Mapeo supplier → MFRID preferido (igual que en insert_data_in_db.py)
SUPPLIER_MFRID = 'HUS'  # PO 93433 es Husqvarna


def resolve_mfrids(part_numbers: list) -> dict:
    """Consulta MySQL ERP y devuelve {partnumber: [mfrid, ...]} con todos los MFRIDs."""
    result = {}
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        placeholders = ','.join(['%s'] * len(part_numbers))
        cur.execute(
            f"SELECT PARTNUMBER, MFRID FROM prontoweb.product WHERE PARTNUMBER IN ({placeholders})",
            part_numbers
        )
        for row in cur.fetchall():
            pn = row['PARTNUMBER']
            if pn not in result:
                result[pn] = []
            result[pn].append(row['MFRID'])
        cur.close()
        conn.close()
        print(f"✅ MySQL ERP: {len(result)}/{len(part_numbers)} parts con MFRID")
    except Exception as e:
        print(f"❌ MySQL ERP error: {e}")
    return result


def query_crossovers_and_packs(mfrid_map: dict) -> dict:
    """
    Para cada partnumber intenta crossover/packs priorizando SUPPLIER_MFRID,
    luego el resto de MFRIDs disponibles.
    Devuelve {partnumber: {'mfrid_used': ..., 'all_mfrids': [...], 'crossover': [...], 'packs': [...]}}
    """
    results = {}
    found_cross = 0
    found_packs = 0

    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        for partnumber, mfrids in mfrid_map.items():
            # Ordenar: preferir SUPPLIER_MFRID primero
            ordered = sorted(mfrids, key=lambda m: (0 if m == SUPPLIER_MFRID else 1, m))
            entry = {'all_mfrids': mfrids, 'mfrid_used': None, 'crossover': [], 'packs': []}

            for mfrid in ordered:
                # ── Crossovers ───────────────────────────────────────────
                cur.execute(
                    """
                    SELECT mfr_cross, partnumber_cross, priority, notes
                    FROM product_crossover
                    WHERE mfr = %s AND partnumber = %s
                    ORDER BY priority
                    """,
                    (mfrid, partnumber)
                )
                rows = cur.fetchall()
                if rows:
                    entry['crossover'] = [
                        {'mfr': r[0], 'partnumber': r[1], 'priority': r[2], 'notes': r[3] or ''}
                        for r in rows
                    ]
                    found_cross += 1

                # ── Packs ────────────────────────────────────────────────
                cur.execute(
                    """
                    SELECT mfr_pack, partnumber_pack, pack_qty, notes
                    FROM product_packs
                    WHERE mfr = %s AND partnumber = %s
                    """,
                    (mfrid, partnumber)
                )
                rows = cur.fetchall()
                if rows:
                    entry['packs'] = [
                        {'mfr': r[0], 'partnumber': r[1], 'pack_qty': r[2], 'notes': r[3] or ''}
                        for r in rows
                    ]
                    found_packs += 1

                if entry['crossover'] or entry['packs']:
                    entry['mfrid_used'] = mfrid
                    break  # ya encontramos datos con este MFRID

            results[partnumber] = entry

        cur.close()
        conn.close()
        print(f"✅ PostgreSQL: {found_cross} con crossover(s) | {found_packs} con pack(s)")

    except Exception as e:
        print(f"❌ PostgreSQL error: {e}")

    return results


if __name__ == "__main__":
    # ── Test puntual: BRS / 491055T ──────────────────────────────────────
    TEST_MFR  = 'BRS'
    TEST_PART = '491055T'

    print("=" * 60)
    print(f"🔍 Test puntual: mfr='{TEST_MFR}' | partnumber='{TEST_PART}'")
    print("=" * 60)

    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        # Crossovers
        cur.execute(
            "SELECT mfr_cross, partnumber_cross, priority, notes FROM product_crossover WHERE mfr = %s AND partnumber = %s ORDER BY priority",
            (TEST_MFR, TEST_PART)
        )
        rows = cur.fetchall()
        print(f"\n🔀 product_crossover ({len(rows)} fila(s)):")
        for r in rows:
            print(f"   mfr_cross={r[0]} | partnumber_cross={r[1]} | priority={r[2]} | notes={r[3]}")
        if not rows:
            print("   (sin registros)")

        # Packs
        cur.execute(
            "SELECT mfr_pack, partnumber_pack, pack_qty, notes FROM product_packs WHERE mfr = %s AND partnumber = %s",
            (TEST_MFR, TEST_PART)
        )
        rows = cur.fetchall()
        print(f"\n📦 product_packs ({len(rows)} fila(s)):")
        for r in rows:
            print(f"   mfr_pack={r[0]} | partnumber_pack={r[1]} | pack_qty={r[2]} | notes={r[3]}")
        if not rows:
            print("   (sin registros)")

        # Totales generales
        cur.execute("SELECT COUNT(*) FROM product_crossover WHERE mfr = %s", (TEST_MFR,))
        print(f"\n📊 Total en product_crossover con mfr='{TEST_MFR}': {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM product_packs WHERE mfr = %s", (TEST_MFR,))
        print(f"📊 Total en product_packs     con mfr='{TEST_MFR}': {cur.fetchone()[0]}")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Error: {e}")

    # 1. Resolver MFRIDs desde MySQL
    print("\n📦 Paso 1: Resolución de MFRID desde MySQL ERP...")
    mfrid_map = resolve_mfrids(PART_NUMBERS)

    # Mostrar los que no se encontraron
    not_found = [p for p in PART_NUMBERS if p not in mfrid_map]
    if not_found:
        print(f"⚠️  Sin MFRID en ERP ({len(not_found)}): {not_found}")

    # 2. Consultar crossovers y packs en PostgreSQL
    print("\n🔗 Paso 2: Consulta de crossovers y packs en PostgreSQL...")
    data = query_crossovers_and_packs(mfrid_map)

    # 3. Mostrar resultados
    print("\n" + "=" * 60)
    print("📊 RESULTADOS")
    print("=" * 60)

    with_crossover = {p: d for p, d in data.items() if d['crossover']}
    with_packs     = {p: d for p, d in data.items() if d['packs']}
    no_data        = {p: d for p, d in data.items() if not d['crossover'] and not d['packs']}

    print(f"\n🔀 Con CROSSOVER ({len(with_crossover)}):")
    for part, d in with_crossover.items():
        for c in d['crossover']:
            print(f"   [{d['mfrid_used']}] {part}  →  {c['mfr']}/{c['partnumber']}  (prioridad={c['priority']}) {c['notes']}")

    print(f"\n📦 Con PACKS ({len(with_packs)}):")
    for part, d in with_packs.items():
        for pk in d['packs']:
            print(f"   [{d['mfrid_used']}] {part}  →  pack_qty={pk['pack_qty']}  {pk['notes']}")

    print(f"\n⬜ Sin datos en crossover/packs: {len(no_data)} parts")

    print("\n" + "=" * 60)
    print(f"✅ Total: {len(data)} | 🔀 Crossover: {len(with_crossover)} | 📦 Packs: {len(with_packs)}")
    print("=" * 60)
