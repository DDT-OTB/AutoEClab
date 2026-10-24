import React, {useState} from 'react';
import {ArrowDown,ArrowUp,Boxes,ChevronLeft,Copy,Download,GripVertical,Pencil,Plus,Save,Settings2,Trash2,Upload,Workflow} from 'lucide-react';
import FlowStep, {ActionIcon} from './FlowStep.jsx';
import ActionInspector from './ActionInspector.jsx';
import {fluidLabels,operationLabels} from './model.js';
import {isMeasurement,stepSummary} from './cv.js';
import {isPreflight} from './preflight.js';
import {PreflightEditor,preflightStepLabels} from './PreflightBlock.jsx';

export function BlockPalette({blocks,templates,measurementTemplates,activeBlock,shortNames,onAddBlock,onAddAction,onImport,onNew,mode,onMode,elementRef,canManage,onManage,onEditTemplate,debug=false,hasPreflight=false,onAddPreparation,onAddHoming}) {
  return <aside className="palette block-palette" ref={elementRef}>
    <div className="panel-heading"><span>编排组件</span><span className="tiny-label">LIBRARY</span></div>
    <div className="palette-tabs" role="tablist" aria-label="编排组件类型">
      <button role="tab" aria-selected={mode==='blocks'} onClick={()=>onMode('blocks')}>功能块</button>
      <button role="tab" aria-selected={mode==='actions'} onClick={()=>onMode('actions')}>动作</button>
    </div>
    <p className="panel-intro">{mode==='blocks'?'先添加功能块，再编辑块内动作。':'将动作加入当前选中的功能块。'}</p>
    {mode==='blocks'? <>
      {canManage && <button className="palette-library-manage" onClick={onManage}><Settings2 size={14}/>管理功能块模板</button>}
      {blocks.map(block=><div className={'block-template-row'+(canManage?' managed':'')} key={block.id}><button className="palette-item block-template" onClick={()=>onAddBlock(block)}>
        <span className="palette-icon"><Boxes size={17}/></span><span><strong>{isPreflight(block)?'前置准备':block.id}</strong><small>{isPreflight(block)?'替换前置设置':block.steps.length+' 个动作 · 顺序执行'}</small></span><Plus size={15}/>
      </button>{canManage && <button className="block-template-edit" onClick={()=>onEditTemplate(block.id)} aria-label={'编辑模板 '+block.id} title="编辑此功能块模板"><Pencil size={13}/></button>}</div>)}
      <button className="palette-item" onClick={onNew}><span className="palette-icon"><Plus size={17}/></span><span><strong>空白功能块</strong><small>自行组合动作</small></span></button>
      <button className="palette-import" onClick={onImport}><Upload size={14}/>导入功能块 JSON</button>
      {debug && !hasPreflight && <button className="palette-import" onClick={onAddPreparation}><Plus size={14}/>可选：添加前置准备</button>}
      <div className="palette-tip"><Workflow size={16}/><span>实验由功能块组成<br/>功能块由动作组成</span></div>
    </>:<>
      <div className="palette-target">{isPreflight(activeBlock)?'前置准备步骤固定，请选择其他功能块。':activeBlock?'加入：'+activeBlock.id:'请先添加并选择一个功能块'}</div>
      {Object.entries({...templates,...measurementTemplates}).map(([id,template])=><button disabled={!activeBlock || isPreflight(activeBlock)} className={'palette-item fluid-'+(isMeasurement({device:id})?'measurement':template.action.fluid || 'valve')} key={id} onClick={()=>onAddAction(id)}>
        <span className="palette-icon"><ActionIcon kind={template.device.kind} size={17}/></span><span><strong>{shortNames[id] || id}</strong><small>{operationLabels[template.action.operation]} · {fluidLabels[template.action.fluid] || (id==='valve'?'液体入口':'参数草稿')}</small></span><Plus size={15}/>
      </button>)}
      {debug && templates.syringe_c && <button className="palette-item" disabled={!activeBlock || isPreflight(activeBlock)} onClick={onAddHoming}><span className="palette-icon"><ActionIcon kind="syringe" size={17}/></span><span><strong>注射泵回零</strong><small>显式机械回零 · 需确认接液</small></span><Plus size={15}/></button>}
    </>}
  </aside>;
}

export function BlockCanvas({blocks,selected,runningIndex,templates,shortNames,onSelect,onMove,onCopy,onDelete,onNew,debug=false}) {
  const [dragKey,setDragKey]=useState(null);
  let offset=0;
  return <div className="flow-canvas block-canvas">
    <div className="canvas-caption"><span><Workflow size={15}/>功能块顺序</span><span>选择功能块，在右侧编辑</span></div>
    {!blocks.some(block=>!isPreflight(block)) && <div className="empty-canvas"><span><Boxes size={30}/></span><h3>添加{debug?'调试':'实验'}功能块</h3><p>{debug?'可以组合多个功能块和动作；前置准备为可选。':'必需的前置准备已加入；继续添加实验动作。'}</p></div>}
    <div className="flow-start"><span/>{debug?'调试':'实验'}开始</div>
    {blocks.map((block,index)=>{
      const fixed=isPreflight(block),start=offset;if(!fixed)offset+=block.steps.length;
      const running=!fixed && runningIndex>=start && runningIndex<offset;
      const actionCount=block.steps.length;
      return <div key={block._key} className="step-wrap block-wrap" onDragOver={event=>event.preventDefault()} onDrop={event=>{
        event.preventDefault();const from=blocks.findIndex(item=>item._key===dragKey);if(from>=0) onMove(from,index);setDragKey(null);
      }}>
        <div className="timeline-number">{String(index+1).padStart(2,'0')}</div>
        <article className={'function-block-card'+(selected===block._key?' selected':'')+(running?' step-running':'')}>
          <div className="block-card-top">{!fixed && <button className="drag-handle" draggable aria-label={'拖动第 '+(index+1)+' 个功能块'} onDragStart={event=>{event.dataTransfer.setData('text/plain',block._key);setDragKey(block._key);}} onDragEnd={()=>setDragKey(null)}><GripVertical size={16}/></button>}<span>{fixed?'前置准备 · '+(debug?'已选':'必需'):'功能块 '+(index+1)}</span><div className="block-card-actions">
            {!fixed && <><button disabled={index===(isPreflight(blocks[0])?1:0)} onClick={()=>onMove(index,index-1)} aria-label="上移功能块"><ArrowUp size={13}/></button><button disabled={index===blocks.length-1} onClick={()=>onMove(index,index+1)} aria-label="下移功能块"><ArrowDown size={13}/></button><button onClick={()=>onCopy(block._key)} aria-label="复制功能块"><Copy size={13}/></button></>}{(!fixed || debug) && <button onClick={()=>onDelete(block._key)} aria-label="删除功能块"><Trash2 size={13}/></button>}
          </div></div>
          <button className="block-select" aria-pressed={selected===block._key} onClick={()=>onSelect(block._key)}>
            <span className="block-card-title"><Boxes size={18}/><strong>{block.id}</strong></span><span className="block-description">{block.description || 'No description'}</span>
            <span className="block-action-count">{actionCount} 个{fixed?'前置步骤':'动作'}{running?' · 动作 '+(runningIndex-start+1)+' 执行中':''}</span>
            <span className="block-action-preview">{block.steps.slice(0,3).map((step,si)=><span key={step._key}><i>{si+1}</i>{fixed?preflightStepLabels[step.operation]:shortNames[step.device] || step.device}{!fixed && <small>{stepSummary(step,templates[step.device])}</small>}</span>)}{actionCount>3 && <span className="block-more-actions">另有 {actionCount-3} 个动作</span>}</span>
          </button>
        </article>
      </div>;
    })}
    <button className="append-step" onClick={onNew}><Plus size={16}/>添加空白功能块</button>
    <div className="flow-end">{debug?'调试':'实验'}结束</div>
  </div>;
}

export function BlockInspector({block,blockIndex,selectedStep,templates,names,shortNames,onBlockChange,onSelectAction,onUpdateAction,onMoveAction,onCopyAction,onDeleteAction,onAddAction,onExport,cellBudget,offset,runningIndex,canManage,onSaveTemplate,safetyPolicy,debug=false}) {
  if(!block) return <div className="inspector-empty"><Boxes size={30}/><h3>选择一个功能块</h3><p>实验由多个功能块顺序组成。<br/>在这里编辑块内动作与参数。</p></div>;
  if(isPreflight(block))return <div className="block-editor"><div className="block-editor-heading"><h3>前置准备 · {debug?'已选':'必需'}</h3><p>{block.id}</p></div><PreflightEditor block={block} onChange={onBlockChange} safetyPolicy={safetyPolicy}/>{canManage && <button className="block-save-template" onClick={onSaveTemplate}><Save size={13}/>保存到功能块库</button>}<button className="block-export" onClick={onExport}><Download size={13}/>导出功能块 JSON</button></div>;
  if(selectedStep) return <div className="block-action-detail">
    <button className="block-back" onClick={()=>onSelectAction(null)}><ChevronLeft size={14}/>返回 {block.id} 的动作列表</button>
    <ActionInspector key={selectedStep._key} step={selectedStep} template={templates[selectedStep.device]} index={offset+block.steps.findIndex(step=>step._key===selectedStep._key)} title={names[selectedStep.device] || selectedStep.device} onChange={onUpdateAction} debug={debug}/>
  </div>;
  return <div className="block-editor">
    <div className="block-editor-heading"><span className="eyebrow">FUNCTION BLOCK {blockIndex+1}</span><h3>功能块与内部动作</h3>{canManage && <button className="block-save-template" onClick={onSaveTemplate}><Save size={13}/>保存到功能块库</button>}<button className="block-export" onClick={onExport}><Download size={13}/>导出功能块 JSON</button></div>
    <label className="parameter-field"><span>功能块 ID · 英文，实验内唯一</span><input value={block.id} maxLength={80} onChange={event=>onBlockChange({id:event.target.value})} aria-label="功能块 ID" spellCheck={false}/></label>
    <label className="parameter-field"><span>功能块说明 · 英文</span><input value={block.description} onChange={event=>onBlockChange({description:event.target.value})} aria-label="功能块说明"/></label>
    <div className="block-actions-heading"><strong>{block.steps.length} 个顺序动作</strong><button onClick={onAddAction}><Plus size={13}/>添加动作</button></div>
    {!block.steps.length && <p className="block-empty-note">此功能块还没有动作。点击“添加动作”，从左侧选择设备。</p>}
    <div className="block-action-list">{block.steps.map((step,index)=><FlowStep key={step._key} step={step} template={templates[step.device]} title={shortNames[step.device] || step.device} index={index} total={block.steps.length}
      cellVolume={cellBudget.steps[offset+index]} cellCapacity={cellBudget.capacity_ml} running={runningIndex===offset+index}
      onSelect={()=>onSelectAction(step._key)} onMove={onMoveAction} onCopy={()=>onCopyAction(step._key)} onDelete={()=>onDeleteAction(step._key)}/>)}</div>
    <p className="parameter-hint">点击动作编辑参数。上下按钮调整块内顺序；复制或导入的功能块独立保存参数。</p>
  </div>;
}
