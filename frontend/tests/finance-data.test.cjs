const test = require('node:test');
const assert = require('node:assert/strict');
const f = require('../app/finance-data.ts');
const record=(id,status,fields={})=>({id,status,extraction:{supplier_name:'Blue Meadow Dairy',invoice_number:id,invoice_date:'2026-11-20',due_date:'2026-11-25',currency:'USD',total:'100.00',...fields}});
test('approved/exported are counted once, rejected/failed/queued are excluded',()=>{
 const m=f.summarize([record('1','approved',{total:'0.10'}),record('2','exported',{total:'0.20'}),record('3','review_required',{total:'90.00'}),record('4','rejected'),record('5','failed'),record('6','queued')],'USD','all','2026-11-22');
 assert.equal(f.formatAmount(m.approvedValue,'USD'),'USD 0.30');assert.equal(f.formatAmount(m.reviewValue,'USD'),'USD 90.00');assert.equal(m.cleared.length,2);assert.equal(m.soon.length,3);
});
test('currency isolation and missing totals are not zero-valued records',()=>{
 const m=f.summarize([record('1','approved',{total:'2.00'}),record('2','approved',{currency:'EUR'}),record('3','approved',{total:null}),record('4','approved',{total:'2.001'}),record('5','approved',{currency:null})],'USD','all','2026-11-22');
 assert.equal(m.approvedUsable,1);assert.equal(m.cleared.length,5);assert.equal(f.formatAmount(m.approvedValue,'USD'),'USD 2.00');assert.equal(m.suppliers[0].count,3);assert.equal(f.amount(record('j','approved',{currency:'JPY',total:'1200'}),'JPY'),BigInt(1200));
});
test('supplier grouping trims case only, period applies to invoice date',()=>{
 const m=f.summarize([record('1','approved'),record('2','exported',{supplier_name:' blue meadow dairy '}),record('3','approved',{supplier_name:'BlueMeadow Dairy'}),record('4','approved',{invoice_date:'2026-10-01'}),record('5','approved',{invoice_date:null})],'USD','2026-11','2026-11-22');
 assert.equal(m.suppliers.length,2);assert.equal(m.suppliers[0].count,2);assert.equal(m.months.length,1);assert.equal(m.undated,1);assert.equal(m.due.length,5);
});
test('due date boundaries are calendar days and payment status is not inferred',()=>{
 const m=f.summarize([record('1','approved',{due_date:'2026-11-21'}),record('2','exported',{due_date:'2026-11-22'}),record('3','processed',{due_date:'2026-11-29'}),record('4','review_required',{due_date:'2026-11-30'}),record('5','approved',{due_date:null}),record('6','approved',{due_date:'2026-02-30'}),record('7','rejected')],'USD','all','2026-11-22');
 assert.deepEqual(m.soon.map(x=>x.record.id),['2','3']);assert.equal(m.past.length,1);assert.equal(m.missingDue,2);assert.equal(m.due.length,4);
});
test('precision, negative amounts and chart boundaries',()=>{
 assert.equal(f.formatAmount(f.amount(record('1','approved',{total:'-1234.50'}),'USD'),'USD'),'USD -1,234.50');assert.equal(f.barWidth(BigInt(25),BigInt(100)),25);assert.equal(f.barWidth(BigInt(-1),BigInt(100)),0);assert.equal(f.barWidth(BigInt(0),BigInt(0)),0);
});
