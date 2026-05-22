const sql = require('mssql');
const fs = require('fs');

async function run() {
  const c = {
    server: '207.244.233.6',
    port: 1433,
    user: 'ebm_inteligencianegocio',
    password: 'I8u5Z5h#]#G>',
    options: { encrypt: false, trustServerCertificate: true }
  };

  const bancos = ['EBMSimulacaoHML', 'EBMSimulacaoDev'];
  let conectado = false;

  for (const db of bancos) {
    try {
      c.database = db;
      await sql.connect(c);
      console.log('✅ Conectado em:', db);
      conectado = true;

      const r = await sql.query(`
        SELECT 
          ec.Empreendimento,
          ec.DataEntrega,
          ec.DataLancamento,
          ec.DataPrevisaoLancamento,
          i.Nome AS Incorporadora
        FROM EmpreendimentoCadastro ec
        LEFT JOIN Incorporadora i ON ec.IncorporadoraId = i.Id
        WHERE ec.Empreendimento LIKE '%MOOD%'
      `);

      fs.writeFileSync('/tmp/mssql_work/resultado.txt', JSON.stringify(r.recordset, null, 2));
      r.recordset.forEach(e => console.log(JSON.stringify(e)));
      sql.close();
      break;

    } catch (e) {
      console.log('❌ ' + db + ': ' + e.message);
    }
  }

  if (!conectado) console.log('Nenhum banco доступível');
}

run().catch(e => console.error('ERRO:', e.message));