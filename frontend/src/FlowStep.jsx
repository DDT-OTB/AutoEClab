import React from 'react';
import { Activity, ArrowDown, ArrowDownToLine, ArrowUp, Copy, Droplet, GripVertical, SlidersHorizontal, Trash2 } from 'lucide-react';
import { isMeasurement, stepSummary } from './cv.js';
import { fluidLabels, operationLabels } from './model.js';

export function ActionIcon({ kind, size = 20 }) {
  return kind === 'electrochemical' ? <Activity size={size} /> :
    kind === 'valve' ? <SlidersHorizontal size={size} /> : kind === 'syringe' ? <ArrowDownToLine size={size} /> : <Droplet size={size} />;
}

export default function FlowStep({ step, index, template, title, selected, running, total, onSelect, onMove, onCopy, onDelete, onDragStart, onDragEnd,cellVolume,cellCapacity }) {
  const measurement = isMeasurement(step);
  return <article className={'step-card fluid-' + (measurement ? 'measurement' : step.fluid || 'valve') +
      (selected ? ' selected' : '') + (running ? ' step-running' : '')}>
    {onDragStart && <button type="button" className="drag-handle" draggable onDragStart={onDragStart} onDragEnd={onDragEnd} aria-label={'拖动第 ' + (index+1) + ' 步'}><GripVertical size={17} /></button>}
    <button type="button" className="step-select" onClick={onSelect} aria-pressed={selected} aria-label={'编辑第 ' + (index+1) + ' 步参数'}>
      <span className="step-icon"><ActionIcon kind={template?.device.kind} size={18} /></span>
      <span className="step-content"><span className="step-title"><strong>{title}</strong><span className="step-operation">{operationLabels[step.operation] || step.operation}</span>
        {measurement ? <span className="measurement-draft-tag">CV</span> : step.fluid && <span className="fluid-tag">{fluidLabels[step.fluid] || step.fluid}</span>}
        {running && <span className="live-step-tag"><span className="status-dot green pulse-dot" />执行中</span>}</span>
        <span className="step-brief">{stepSummary(step,template)}</span>
        {cellVolume && <small className={'cell-step-volume '+(cellVolume.over_capacity?'over-capacity':'')}>预计池内 {cellVolume.after_ml} / {cellCapacity} mL</small>}
      </span>
    </button>
    <div className="step-actions">
      <button type="button" disabled={index===0} onClick={() => onMove(index,index-1)} aria-label={'上移第 ' + (index+1) + ' 步'}><ArrowUp size={14} /></button>
      <button type="button" disabled={index===total-1} onClick={() => onMove(index,index+1)} aria-label={'下移第 ' + (index+1) + ' 步'}><ArrowDown size={14} /></button>
      <button type="button" onClick={onCopy} aria-label={'复制第 ' + (index+1) + ' 步'}><Copy size={14} /></button>
      <button type="button" className="delete-step" onClick={onDelete} aria-label={'删除第 ' + (index+1) + ' 步'}><Trash2 size={14} /></button>
    </div>
  </article>;
}
