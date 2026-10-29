import {useEffect,useRef,useState} from 'react';
import {appApi} from './api.js';
import {activeMeasurementStates} from './measurements.js';
import {createMeasurementResource} from './measurementResource.js';

const resources=new WeakMap();
function resource(api) {
  if(!resources.has(api))resources.set(api,createMeasurementResource(api));
  return resources.get(api);
}

export function useMeasurementDetail(id,{liveMeasurement,fresh=true,api=appApi}={}) {
  const store=resource(api),[value,setValue]=useState(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
  const latest=useRef({liveMeasurement,fresh});latest.current={liveMeasurement,fresh};
  const revision=id && liveMeasurement?.metadata?.id===id?liveMeasurement.metadata.version:undefined;
  const detail=value?.measurement?.id===id?value:store.peek(id);
  useEffect(()=>{
    if(!id)return;
    setError('');
    let closed=false,busy=false;
    async function read(force=false) {
      if(closed || busy)return;busy=true;
      const current=latest.current,live=current.liveMeasurement?.metadata?.id===id?current.liveMeasurement:null;
      try {
        const data=await store.read(id,current.fresh?live?.metadata?.version:undefined,{force});
        if(!closed){setValue(data);setError('');}
      }catch(failure){if(!closed){if(failure.status===404){store.invalidate(id);setValue(null);}setError(failure.message);}}
      finally{busy=false;}
    }
    read(true);
    const timer=setInterval(()=>{
      const cached=store.peek(id);
      if(!cached || activeMeasurementStates.includes(cached.measurement.state))read();
    },2000);
    return()=>{closed=true;clearInterval(timer);};
  },[id,revision,refresh,api,store]);
  function reload(){if(id)store.invalidate(id);setRefresh(value=>value+1);}
  return {detail,error,loading:!!id&&!detail&&!error,reload};
}
