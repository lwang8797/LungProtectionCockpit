const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
let m, n = 0, bad = 0;
while ((m = re.exec(src))) {
  n++;
  const body = m[1];
  if (!body.trim()) continue;
  try { new Function(body); console.log(`  [OK] script #${n} (${body.length} chars)`); }
  catch (e) { bad++; console.log(`  [FAIL] script #${n}: ${e.message}`); }
}
console.log(bad ? `语法错误 ${bad} 处` : `全部 ${n} 个 script 块语法通过`);
process.exit(bad ? 1 : 0);
