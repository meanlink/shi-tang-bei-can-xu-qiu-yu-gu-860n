import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const mealCatalog=()=>api('/meals/catalog');
export const dishes={
 list:()=>api('/meals/dishes'),
 create:data=>api('/meals/dishes',{method:'POST',body:JSON.stringify(data)}),
 patch:(id,data)=>api('/meals/dishes/'+id,{method:'PATCH',body:JSON.stringify(data)})
};
export const mealBatches={
 list:()=>api('/meals/batches'),
 get:id=>api('/meals/batches/'+id),
 create:data=>api('/meals/batches',{method:'POST',body:JSON.stringify(data)}),
 configure:(id,data)=>api('/meals/batches/'+id+'/configuration',{method:'PUT',body:JSON.stringify(data)}),
 allocate:(id,data)=>api('/meals/batches/'+id+'/allocate',{method:'POST',body:JSON.stringify(data)}),
 substitute:(id,data)=>api('/meals/batches/'+id+'/substitutions',{method:'POST',body:JSON.stringify(data)}),
 dispatch:(id,data)=>api('/meals/batches/'+id+'/dispatch',{method:'POST',body:JSON.stringify(data)}),
 produce:(id,mealId)=>api('/meals/batches/'+id+'/meals/'+mealId+'/produce',{method:'POST'}),
 recover:id=>api('/meals/batches/'+id+'/recover',{method:'POST'})
};
