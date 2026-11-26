// Read-only reporting over the invoice register. Never infer paid/unpaid status.
export type FinanceRecord = {id:string; status:string; extraction:{supplier_name:string|null; invoice_number:string|null; invoice_date:string|null; due_date:string|null; currency:string|null; total:string|null}|null};
const zero=BigInt(0);
export const approved=(r:FinanceRecord)=>r.status==='approved'||r.status==='exported';
export const review=(r:FinanceRecord)=>r.status==='processed'||r.status==='review_required';
export function currencyOf(r:FinanceRecord){const c=r.extraction?.currency?.trim().toUpperCase();return c&&/^[A-Z]{3}$/.test(c)?c:null;}
export function validDay(v:string|null|undefined):v is string {if(!v||!/^\d{4}-\d{2}-\d{2}$/.test(v))return false;const d=new Date(v+'T00:00:00Z');return !Number.isNaN(+d)&&d.toISOString().slice(0,10)===v;}
export function localDay(d=new Date()){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
export function daysBetween(a:string,b:string){return Math.round((Date.parse(b+'T00:00:00Z')-Date.parse(a+'T00:00:00Z'))/86400000);}
export function precision(currency:string){return new Intl.NumberFormat('en-US',{style:'currency',currency}).resolvedOptions().maximumFractionDigits??2;}
export function amount(r:FinanceRecord,currency:string):bigint|null {if(currencyOf(r)!==currency)return null;const v=r.extraction?.total; if(typeof v!=='string'||v.length>80)return null;const m=/^([+-]?)(\d+)(?:\.(\d+))?$/.exec(v.trim());if(!m)return null;const p=precision(currency);const fraction=m[3]||'';if(/[^0]/.test(fraction.slice(p)))return null;return BigInt((m[1]==='-'?'-':'')+m[2]+fraction.slice(0,p).padEnd(p,'0'));}
export function formatAmount(v:bigint,currency:string){const p=precision(currency);const raw=(v<zero?-v:v).toString().padStart(p+1,'0');const whole=p?raw.slice(0,-p):raw;return `${currency} ${v<zero?'-':''}${whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')}${p?'.'+raw.slice(-p):''}`;}
export function summarize(records:FinanceRecord[],currency:string,period:string,today:string){
 const inPeriod=(r:FinanceRecord)=>period==='all'||(validDay(r.extraction?.invoice_date)&&r.extraction.invoice_date.slice(0,7)===period);
 const scoped=records.filter(inPeriod);const cleared=scoped.filter(approved);const pending=scoped.filter(review);
 const sum=(rs:FinanceRecord[])=>rs.reduce((a,r)=>a+(amount(r,currency)??zero),zero);
 const usable=(rs:FinanceRecord[])=>rs.filter(r=>amount(r,currency)!==null).length;
 const suppliers=new Map<string,{name:string;count:number;total:bigint;usable:number;ids:string[]}>();
 const months=new Map<string,bigint>();
 for(const r of cleared){const v=amount(r,currency); if(currencyOf(r)!==currency)continue;const name=r.extraction?.supplier_name?.trim()||'Supplier not recorded';const key=name.toLowerCase();const group=suppliers.get(key)||{name,count:0,total:zero,usable:0,ids:[]};group.count++;group.ids.push(r.id);if(v!==null){group.total+=v;group.usable++;}suppliers.set(key,group);if(v!==null&&validDay(r.extraction?.invoice_date)){const m=r.extraction.invoice_date.slice(0,7);months.set(m,(months.get(m)??zero)+v);}}
 const due=records.filter(r=>(approved(r)||review(r))&&validDay(r.extraction?.due_date)).map(r=>({record:r,days:daysBetween(today,r.extraction!.due_date!)})).sort((a,b)=>a.days-b.days);
 return {cleared,pending,approvedValue:sum(cleared),reviewValue:sum(pending),approvedUsable:usable(cleared),reviewUsable:usable(pending),suppliers:[...suppliers.values()].sort((a,b)=>a.total>b.total?-1:a.total<b.total?1:a.name.localeCompare(b.name)),months:[...months].sort(([a],[b])=>a.localeCompare(b)),due,soon:due.filter(d=>d.days>=0&&d.days<=7),past:due.filter(d=>d.days<0),missingDue:records.filter(r=>(approved(r)||review(r))&&!validDay(r.extraction?.due_date)).length,undated:records.filter(r=>approved(r)&&!validDay(r.extraction?.invoice_date)).length};
}
export function barWidth(v:bigint,max:bigint){return max>zero&&v>zero?Number(v*BigInt(10000)/max)/100:0;}
