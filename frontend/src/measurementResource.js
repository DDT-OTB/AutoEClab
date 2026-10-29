import {activeMeasurementStates} from './measurements.js';

export function createMeasurementResource(api,{limit=8}={}) {
  const cache=new Map(),pending=new Map();
  return {
    peek:id=>cache.get(id)?.value || null,
    async read(id,revision,{force=false}={}) {
      const entry=cache.get(id);
      if(!force && entry && (revision?entry.version===revision:!activeMeasurementStates.includes(entry.value.measurement.state)))return entry.value;
      if(pending.has(id))return pending.get(id);
      const promise=Promise.resolve().then(()=>api.measurement(id,{signal:AbortSignal.timeout(20000)})).then(value=>{
        const old=cache.get(id);
        if(old && old.value.measurement.updated_at>value.measurement.updated_at)return old.value;
        cache.delete(id);cache.set(id,{version:value.measurement.version,value});
        while(cache.size>limit)cache.delete(cache.keys().next().value);
        return value;
      }).finally(()=>pending.delete(id));
      pending.set(id,promise);
      return promise;
    },
    invalidate(id){cache.delete(id);},
  };
}
