import sys
sys.path.append('app')
from utils.bd_mysql import get_db_connection, get_pg_connection

# Ver los MFRIDs que resuelve el ERP
conn = get_db_connection()
cur = conn.cursor(dictionary=True)
parts = ['501051401','501269705','504597008','510244105','512846401','532109808','535280001','537024003']
ph = ','.join(['%s']*len(parts))
cur.execute(f'SELECT PARTNUMBER, MFRID FROM prontoweb.product WHERE PARTNUMBER IN ({ph})', parts)
print('=== MFRIDs desde MySQL ERP ===')
for r in cur.fetchall():
    print(f"  {r['PARTNUMBER']} -> '{r['MFRID']}'")
cur.close()
conn.close()

# Ver los MFRs que existen en PostgreSQL
conn2 = get_pg_connection()
cur2 = conn2.cursor()
cur2.execute('SELECT DISTINCT mfr FROM product_crossover ORDER BY mfr LIMIT 30')
print('\n=== MFRs en product_crossover (muestra) ===')
for r in cur2.fetchall():
    print(f'  {r[0]}')
cur2.execute('SELECT DISTINCT mfr FROM product_packs ORDER BY mfr LIMIT 30')
print('\n=== MFRs en product_packs (muestra) ===')
for r in cur2.fetchall():
    print(f'  {r[0]}')
cur2.close()
conn2.close()
