"""Split logical syringe doses into bounded, validated aspiration/dispense strokes."""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import json
import math
from pathlib import Path


def load_policy():
    value=json.loads((Path(__file__).resolve().parents[2]/'config/syringe_policy.json').read_text())
    if (set(value)!={'schema_version','max_stroke_volume_ml','max_strokes_per_action'} or
            type(value['schema_version']) is not int or value['schema_version']!=1 or
            type(value['max_stroke_volume_ml']) not in (int,float) or not math.isfinite(value['max_stroke_volume_ml']) or value['max_stroke_volume_ml']<=0 or
            type(value['max_strokes_per_action']) is not int or not 1<=value['max_strokes_per_action']<=1024):
        raise ValueError('Invalid syringe stroke policy')
    return value


def stroke_limit(device,policy=None):
    policy=policy or load_policy()
    capacity=Decimal(str(device['capacity_ml']))
    maximum=min(capacity,Decimal(str(policy['max_stroke_volume_ml'])))
    steps=int((maximum*device['stroke_steps']/capacity).to_integral_value(rounding=ROUND_FLOOR))
    if steps<1:raise ValueError('单次注射上限小于注射泵一步，请检查配置。')
    return float(Decimal(steps)*capacity/device['stroke_steps']),steps


def split_volume(volume,device,policy=None):
    policy=policy or load_policy()
    try:valid=type(volume) in (int,float) and math.isfinite(volume) and volume>0
    except OverflowError:valid=False
    if not valid:raise ValueError('注射总体积必须为正的有限数值。')
    maximum,_=stroke_limit(device,policy)
    remaining=Decimal(str(volume));limit=Decimal(str(maximum))
    if remaining>limit*policy['max_strokes_per_action']:raise ValueError('注射分次数超过服务器上限。')
    result=[]
    while remaining>0:
        amount=min(remaining,limit);result.append(float(amount));remaining-=amount
    return result


def validate_snapshot_policy(snapshot):
    if any(step.get('action')=='syringe_transfer' for step in snapshot['plan']['steps']):
        if snapshot.get('syringe_policy')!=load_policy():
            raise ValueError('注射泵分次规则已更新，请重新准备实验。')


def compile_native_plan(devices,sequence,config):
    """Use the installed controller for every physical stroke, then retain logical IDs."""
    config.validate_devices(devices)
    policy=load_policy();expanded=[];groups=[];reserved={s.get('id') for s in sequence['steps']}
    for index,original in enumerate(sequence['steps'],1):
        start=len(expanded)
        if original.get('action')=='syringe_transfer':
            device=devices['devices'][original['device']]
            amounts=split_volume(original['volume_ml'],device,policy)
        else:amounts=None
        if amounts and len(amounts)>1:
            for number,amount in enumerate(amounts,1):
                name=f'stroke_{index:04d}_{number:04d}'
                while name in reserved:name+='x'
                reserved.add(name)
                expanded.append({**deepcopy(original),'id':name,'volume_ml':amount,
                    'description':f"{original['description']} (stroke {number}/{len(amounts)}: {amount:g} mL)"})
        else:expanded.append(deepcopy(original))
        groups.append((original,start,len(expanded)))
    plan=config.compile_plan(devices,{**sequence,'steps':expanded})
    logical=[]
    for index,(original,start,end) in enumerate(groups,1):
        strokes=plan['steps'][start:end]
        if original.get('action')=='syringe_transfer':
            device=devices['devices'][original['device']];_,max_delta=stroke_limit(device,policy)
            if any(stroke['delta_steps']>max_delta for stroke in strokes):raise ValueError('注射步数超出单次注射上限。')
        if len(strokes)==1:
            logical.append({**strokes[0],'index':index});continue
        logical.append({**strokes[0],'id':original['id'],'index':index,'volume_ml':original['volume_ml'],'description':original['description'],
            'delta_steps':max(stroke['delta_steps'] for stroke in strokes),
            'achievable_volume_ml':math.fsum(stroke['achievable_volume_ml'] for stroke in strokes),
            'estimated_movement_s':math.fsum(stroke['estimated_movement_s'] for stroke in strokes),
            'stroke_limit_ml':policy['max_stroke_volume_ml'],'stroke_count':len(strokes),
            'stroke_plan':deepcopy(strokes)})
    plan['steps']=logical
    return plan
