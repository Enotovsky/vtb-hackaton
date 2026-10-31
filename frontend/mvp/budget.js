import {periodOps, total, monthEnd} from './ui.js';

export const budgetGroups = [
  {id:'food', name:'Продукты', icon:'food', color:'#0671fa', soft:'#eaf3ff', categories:['food']},
  {id:'home', name:'Дом', icon:'home', color:'#47b5ce', soft:'#e9f7fb', categories:['home']},
  {id:'kids', name:'Дети', icon:'child', color:'#a18bd8', soft:'#f1edfa', categories:['sections','clubs']},
  {id:'fun', name:'Досуг', icon:'gift', color:'#efa770', soft:'#fff2e7', categories:['fun']}
];
export function familyBudget(s, c) {
  const plan = s.monthlyBudgets?.find(b=>b.month===c.month);
  const ops = periodOps(s,c.from,c.to);
  const groups = budgetGroups.map(g=>({...g, planned:plan?.limits[g.id]||0, spent:total(ops.filter(o=>g.categories.includes(o.category)))}));
  const planned = groups.reduce((n,g)=>n+g.planned,0), spent = total(ops);
  return {plan,groups,planned,spent,remaining:planned-spent,fullMonth:c.from===c.month+'-01'&&c.to===c.month+'-'+monthEnd(c.month)};
}
