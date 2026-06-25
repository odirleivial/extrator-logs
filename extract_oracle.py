import oracledb
import csv

def executar_consulta_e_exportar_csv(dsn, usuario, senha, sql, caminho_csv):
    conn = oracledb.connect(user=usuario, password=senha, dsn=dsn)
    cur = conn.cursor()
    cur.execute(sql)
    colunas = [desc[0] for desc in cur.description]
    with open(caminho_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';', lineterminator='\n')
        writer.writerow(colunas)
        for row in cur:
            writer.writerow(row)
    cur.close()
    conn.close()