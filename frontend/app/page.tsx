'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import {
  ReactFlow,
  Background,
  Handle,
  Position,
  MarkerType,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';

// --- API ------------------------------------------------------------------
const API = 'http://localhost:8000';
const TICKET_KEY_RE = /^[A-Z][A-Z0-9_]*-\d+$/;

// --- Types ----------------------------------------------------------------
type Step =
  | 'idle'
  | 'extract'
  | 'prepare'
  | 'condense'
  | 'semantic'
  | 'historical'
  | 'merge'
  | 'llm_select'
  | 'finalize'
  | 'done'
  | 'error';
type Tab =
  | 'selection'
  | 'comparison'
  | 'llmPasses'
  | 'retrieval'
  | 'vsCandidates'
  | 'historicCandidates'
  | 'faissHits'
  | 'mergedCandidates'
  | 'reference'
  | 'raw';

interface IdeaCard {
  doc_id: string;
  display_name: string;
  has_mapping: boolean;
  file_name?: string;
  extension?: string;
}

interface Candidate {
  entity_id: string;
  entity_name: string;
  score?: number;
  ranking_score?: number;
  _aggregated_best_score?: number;
  _support_count?: number;
  semantic_score?: number;
  historical_strength?: number;
  support_count?: number;
  direct_count?: number;
  implied_count?: number;
  best_support_score?: number;
  avg_support_score?: number;
  from_semantic?: boolean;
  from_historical?: boolean;
  bucket?: string;
  description?: string;
  candidate_source?: string;
  candidate_status?: string;
  candidate_status_reason?: string;
  historical_reasons?: string[];
  [k: string]: unknown;
}

interface SelectedVS {
  entity_id: string;
  entity_name: string;
  confidence: number;
  reason: string;
  category?: string;
  is_match?: boolean;
  matched_to_ground_truth?: string;
}

interface RejectedVS { entity_id: string; entity_name: string; reason: string }
interface CanonicalVS { id: string; name: string; category: string }

interface HistoricalTicketHit {
  ticket_id: string;
  best_score?: number;
  title?: string;
  summary_preview?: string;
  value_stream_names?: string[];
  direct_vs_names?: string[];
  implied_vs_names?: string[];
  label_source?: string;
}

interface PipelineResult {
  selected_value_streams: SelectedVS[];
  rejected_candidates: RejectedVS[];
  candidates_used?: Candidate[];
  candidate_value_streams?: Candidate[];
  semantic_candidate_value_streams?: Candidate[];
  historical_candidate_value_streams?: Candidate[];
  merged_candidate_value_streams?: Candidate[];
  historical_ticket_hits?: HistoricalTicketHit[];
  raw_response?: unknown;
  direct_llm_output?: unknown;
  historical_llm_output?: unknown;
  historical_excluded_ticket_ids?: string[];
  ground_truth?: string[];
  ground_truth_title?: string;
  source_doc_id?: string;
  canonical_value_streams?: CanonicalVS[];
}

// --- Colors ---------------------------------------------------------------
// Accent = sky/teal. Success = emerald. Warn = amber. Error = red.
// Zero purple/indigo anywhere.

// --- Icons (inline SVG, Feather-style) ------------------------------------
type Ico = 'zap' | 'sun' | 'moon' | 'play' | 'loader' | 'search' | 'layers'
         | 'check' | 'x' | 'scale' | 'book' | 'terminal' | 'alert' | 'rocket'
         | 'sliders' | 'file' | 'crosshair' | 'database' | 'cpu' | 'award'
         | 'chevDown' | 'grid' | 'bookmark' | 'bell' | 'help' | 'logout';

function I({ n, className = 'w-4 h-4' }: { n: Ico; className?: string }) {
  const p = {
    viewBox: '0 0 24 24', className, fill: 'none', stroke: 'currentColor',
    strokeWidth: '1.75', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  };
  switch (n) {
    case 'zap':       return <svg {...p}><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" /></svg>;
    case 'sun':       return <svg {...p}><circle cx="12" cy="12" r="5" /><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" /></svg>;
    case 'moon':      return <svg {...p}><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" /></svg>;
    case 'play':      return <svg {...p} fill="currentColor" stroke="none"><path d="M6 4l14 8-14 8V4z" /></svg>;
    case 'loader':    return <svg {...p} className={`${className} animate-spin`}><path d="M12 2v4m0 12v4m-7.07-15.07l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83" /></svg>;
    case 'search':    return <svg {...p}><circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" /></svg>;
    case 'layers':    return <svg {...p}><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></svg>;
    case 'check':     return <svg {...p}><path d="M20 6L9 17l-5-5" /></svg>;
    case 'x':         return <svg {...p}><path d="M18 6L6 18M6 6l12 12" /></svg>;
    case 'scale':     return <svg {...p}><path d="M16 3h5v5M8 3H3v5M3 16v5h5M21 16v5h-5" /><path d="M21 3l-6 6M3 3l6 6M3 21l6-6M21 21l-6-6" /></svg>;
    case 'book':      return <svg {...p}><path d="M4 19.5A2.5 2.5 0 016.5 17H20" /><path d="M4 4.5A2.5 2.5 0 016.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15z" /></svg>;
    case 'terminal':  return <svg {...p}><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>;
    case 'alert':     return <svg {...p}><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg>;
    case 'rocket':    return <svg {...p}><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 00-2.91-.09z" /><path d="M12 15l-3-3a22 22 0 012-3.95A12.88 12.88 0 0122 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 01-4 2z" /><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" /></svg>;
    case 'sliders':   return <svg {...p}><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></svg>;
    case 'file':      return <svg {...p}><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>;
    case 'crosshair': return <svg {...p}><circle cx="12" cy="12" r="10" /><line x1="22" y1="12" x2="18" y2="12" /><line x1="6" y1="12" x2="2" y2="12" /><line x1="12" y1="6" x2="12" y2="2" /><line x1="12" y1="22" x2="12" y2="18" /></svg>;
    case 'database':  return <svg {...p}><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" /></svg>;
    case 'cpu':       return <svg {...p}><rect x="4" y="4" width="16" height="16" rx="2" ry="2" /><rect x="9" y="9" width="6" height="6" /><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" /></svg>;
    case 'award':     return <svg {...p}><circle cx="12" cy="8" r="7" /><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88" /></svg>;
    case 'chevDown':  return <svg {...p}><polyline points="6 9 12 15 18 9" /></svg>;
    case 'grid':      return <svg {...p}><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>;
    case 'bookmark':  return <svg {...p}><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z" /></svg>;
    case 'bell':      return <svg {...p}><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 01-3.46 0" /></svg>;
    case 'help':      return <svg {...p}><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>;
    case 'logout':    return <svg {...p}><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>;
    default:          return <svg {...p}><circle cx="12" cy="12" r="1" /></svg>;
  }
}

// --- UI primitives --------------------------------------------------------

function Pill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'sky' | 'green' | 'amber' | 'red' }) {
  const cls: Record<string, string> = {
    neutral: 'bg-zinc-100 text-zinc-700 ring-1 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-700',
    sky:     'bg-teal-50 text-teal-900 ring-1 ring-teal-200 dark:bg-teal-950/50 dark:text-teal-200 dark:ring-teal-800',
    green:   'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-200 dark:ring-emerald-800',
    amber:   'bg-amber-50 text-amber-900 ring-1 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-200 dark:ring-amber-800',
    red:     'bg-rose-50 text-rose-800 ring-1 ring-rose-200 dark:bg-rose-950/40 dark:text-rose-200 dark:ring-rose-800',
  };
  return <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ${cls[tone]}`}>{children}</span>;
}

function Section({ title, icon, badge, children, noPad, subtitle }: {
  title: string; icon: Ico; badge?: React.ReactNode; children: React.ReactNode; noPad?: boolean; subtitle?: string;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.05)] dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-none">
      <header className="flex items-center justify-between gap-3 border-b border-zinc-200 bg-white px-4 py-3.5 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-hcsc text-white shadow-sm dark:bg-teal-700">
            <I n={icon} className="w-4 h-4" />
          </span>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold text-zinc-950 dark:text-zinc-50">{title}</div>
            {subtitle && <div className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{subtitle}</div>}
          </div>
        </div>
        {badge}
      </header>
      <div className={noPad ? '' : 'p-4'}>{children}</div>
    </section>
  );
}

// --- React Flow pipeline --------------------------------------------------

type PipelineStage = 'extract' | 'condense' | 'retrieve' | 'merge' | 'llm_select' | 'finalize';
type StepNodeData = { title: string; subtitle: string; icon: Ico; status: 'idle' | 'active' | 'done' | 'error' };

function StepNode({ data }: NodeProps<Node<StepNodeData>>) {
  const base = 'w-[180px] rounded-md border px-3.5 py-3 shadow-sm transition-all duration-300';
  const v: Record<string, string> = {
    idle:   'border-stone-200 bg-stone-50 text-stone-600 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-400',
    active: 'border-teal-400 bg-teal-50 text-teal-900 shadow-sm dark:border-teal-500 dark:bg-teal-950/35 dark:text-teal-200 dark:shadow-none',
    done:   'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500 dark:bg-emerald-950/40 dark:text-emerald-300',
    error:  'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500 dark:bg-rose-950/40 dark:text-rose-300',
  };

  return (
    <div className={`${base} ${v[data.status]}`}>
      <Handle type="target" position={Position.Left} className="!bg-zinc-300 dark:!bg-zinc-600 !w-2 !h-2 !border-0" />
      <div className="flex items-center gap-2 mb-1">
        {data.status === 'active'
          ? <I n="loader" className="w-3.5 h-3.5" />
          : data.status === 'done'
          ? <I n="check" className="w-3.5 h-3.5" />
          : <I n={data.icon} className="w-3.5 h-3.5" />}
        <span className="text-xs font-bold tracking-tight">{data.title}</span>
      </div>
      <div className="text-[10px] leading-snug opacity-75">{data.subtitle}</div>
      <Handle type="source" position={Position.Right} className="!bg-zinc-300 dark:!bg-zinc-600 !w-2 !h-2 !border-0" />
    </div>
  );
}

const NODE_TYPES = { step: StepNode };

const STEP_DEFS = [
  { id: 'extract',      stage: 'extract'    as PipelineStage, x: 0,   y: 70,  icon: 'file'     as Ico, title: 'Extract',      subtitle: 'Read idea card' },
  { id: 'condense',     stage: 'condense'   as PipelineStage, x: 205, y: 70,  icon: 'zap'      as Ico, title: 'Condense',     subtitle: 'Single retrieval query' },
  { id: 'semantic',     stage: 'retrieve'   as PipelineStage, x: 430, y: 18,  icon: 'database' as Ico, title: 'VS Search',    subtitle: 'Condensed VS query' },
  { id: 'historical',   stage: 'retrieve'   as PipelineStage, x: 430, y: 122, icon: 'layers'   as Ico, title: 'Historical',   subtitle: 'Condensed history query' },
  { id: 'merge',        stage: 'merge'      as PipelineStage, x: 640, y: 70,  icon: 'grid'     as Ico, title: 'Merge',        subtitle: 'Rank candidates' },
  { id: 'directLlm',    stage: 'llm_select' as PipelineStage, x: 850, y: 18,  icon: 'cpu'      as Ico, title: 'Direct LLM',   subtitle: 'Direct fit' },
  { id: 'historicLlm',  stage: 'llm_select' as PipelineStage, x: 850, y: 122, icon: 'book'     as Ico, title: 'Historic LLM', subtitle: 'Pattern fit' },
  { id: 'finalize',     stage: 'finalize'   as PipelineStage, x: 1075,y: 70,  icon: 'award'    as Ico, title: 'Finalize',     subtitle: 'Filter + rescue' },
];

const FLOW_EDGES_BASE: Edge[] = [
  { id: 'e1', source: 'extract',     target: 'condense',    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e2', source: 'condense',    target: 'semantic',    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e3', source: 'condense',    target: 'historical',  markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e4', source: 'semantic',    target: 'merge',       markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e5', source: 'historical',  target: 'merge',       markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e6', source: 'merge',       target: 'directLlm',   markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e7', source: 'merge',       target: 'historicLlm', markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e8', source: 'directLlm',   target: 'finalize',    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e9', source: 'historicLlm', target: 'finalize',    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
];

const PIPELINE_SEQ: PipelineStage[] = ['extract', 'condense', 'retrieve', 'merge', 'llm_select', 'finalize'];

function normalizePipelineStage(step: Step): PipelineStage | null {
  if (step === 'prepare' || step === 'condense') return 'condense';
  if (step === 'semantic' || step === 'historical') return 'retrieve';
  if (step === 'extract' || step === 'merge' || step === 'llm_select' || step === 'finalize') return step;
  return null;
}

function PipelineGraph({ step, stepLabel, stepOnError }: { step: Step; stepLabel: string; stepOnError: Step }) {
  const activeStage = normalizePipelineStage(step === 'error' ? stepOnError : step);
  const activeIdx = activeStage ? PIPELINE_SEQ.indexOf(activeStage) : -1;
  const isDone = step === 'done';
  const isError = step === 'error';

  const nodes = useMemo<Node<StepNodeData>[]>(() =>
    STEP_DEFS.map((d) => {
      const nodeStageIdx = PIPELINE_SEQ.indexOf(d.stage);
      const isActive = d.stage === activeStage && !isDone && !isError;
      return {
      id: d.id,
      type: 'step' as const,
      position: { x: d.x, y: d.y },
      draggable: false,
      selectable: false,
      data: {
        title: d.title,
        subtitle: isActive ? (stepLabel || d.subtitle) : d.subtitle,
        icon: d.icon,
        status: isDone ? 'done'
          : isError ? (nodeStageIdx < activeIdx ? 'done' : nodeStageIdx === activeIdx ? 'error' : 'idle')
          : activeIdx < 0 ? 'idle'
          : nodeStageIdx < activeIdx ? 'done'
          : nodeStageIdx === activeIdx ? 'active'
          : 'idle',
      },
    };
    }),
  [activeStage, activeIdx, isDone, isError, stepLabel]);

  const edges = useMemo<Edge[]>(() =>
    FLOW_EDGES_BASE.map((e) => {
      const sourceStage = STEP_DEFS.find((d) => d.id === e.source)?.stage;
      const targetStage = STEP_DEFS.find((d) => d.id === e.target)?.stage;
      const sourceIdx = sourceStage ? PIPELINE_SEQ.indexOf(sourceStage) : -1;
      const targetIdx = targetStage ? PIPELINE_SEQ.indexOf(targetStage) : -1;
      const completed = isDone || (!isError && targetIdx >= 0 && targetIdx <= activeIdx) || (isError && sourceIdx >= 0 && sourceIdx < activeIdx);
      return {
      ...e,
      animated: completed && !isDone,
      style: { strokeWidth: 2, stroke: completed ? '#10b981' : '#a1a1aa' },
    };
    }),
  [activeIdx, isDone, isError]);

  return (
    <div className="h-[240px] w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1.15, minZoom: 0.6 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        zoomOnPinch={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} />
      </ReactFlow>
    </div>
  );
}

// --- Empty state ----------------------------------------------------------

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-14 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-stone-100 text-stone-500 dark:bg-zinc-800 dark:text-zinc-500">
        <I n="search" className="w-4 h-4" />
      </div>
      <p className="max-w-xs text-sm text-stone-500 dark:text-zinc-400">{text}</p>
    </div>
  );
}

// --- Result panes ---------------------------------------------------------

function SelectionPane({ selected, rejected }: { selected: SelectedVS[]; rejected: RejectedVS[] }) {
  if (!selected.length && !rejected.length) return <Empty text="Run the pipeline to see AI selections." />;
  return (
    <div className="space-y-5">
      <div className="mb-2 flex items-center gap-2"><Pill tone="green">Selected ({selected.length})</Pill></div>
      <div className="space-y-2">
        {[...selected].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0)).map((vs, i) => {
          const pct = Math.round((vs.confidence ?? 0) * 100);
          return (
            <div key={i} className="rounded-md border border-emerald-200 bg-emerald-50/80 p-3.5 dark:border-emerald-800 dark:bg-emerald-950/30">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">{vs.entity_name}</div>
                  <div className="mt-0.5 text-[11px] font-mono text-zinc-400 dark:text-zinc-500">{vs.entity_id}</div>
                </div>
                <span className={`text-xs font-bold ${pct >= 80 ? 'text-emerald-600 dark:text-emerald-400' : pct >= 50 ?
                  'text-amber-600 dark:text-amber-400' : 'text-red-500'}`}>{pct}%</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-emerald-200/70 dark:bg-emerald-900/40">
                <div className="h-full rounded-full bg-hcsc transition-all duration-700 dark:bg-emerald-400" style={{ width: `${pct}%` }} />
              </div>
              {vs.reason && <p className="mt-2 text-xs leading-relaxed text-zinc-600 dark:text-zinc-300 border-t
                border-emerald-200/60 dark:border-emerald-800/40 pt-2">{vs.reason}</p>}
            </div>
          );
        })}
      </div>

      {rejected.length > 0 && (
        <div>
          <div className="mb-2"><Pill tone="red">Rejected ({rejected.length})</Pill></div>
          <div className="space-y-1.5">
            {rejected.map((vs, i) => (
              <div key={i} className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/50">
                <div className="text-sm text-zinc-700 dark:text-zinc-200">{vs.entity_name}</div>
                {vs.reason && <div className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{vs.reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function scoreOf(c: Candidate) {
  return Number(
    c._aggregated_best_score
    ?? c.score
    ?? c.ranking_score
    ?? c.semantic_score
    ?? c.historical_strength
    ?? c.best_support_score
    ?? c.avg_support_score
    ?? c["@search.reranker_score"]
    ?? c["@search.score"]
    ?? 0
  ) || 0;
}

function bucketTone(bucket?: string): 'neutral' | 'sky' | 'green' | 'amber' | 'red' {
  if (bucket === 'semantic_plus_historical') return 'green';
  if (bucket === 'semantic_only' || bucket === 'semantic') return 'sky';
  if (bucket === 'historical_only' || bucket === 'historical') return 'amber';
  return 'neutral';
}

function bucketLabel(candidate: Candidate) {
  const bucket = String(candidate.bucket ?? candidate.candidate_source ?? '').trim();
  if (!bucket) return null;
  if (bucket === 'semantic_plus_historical') return 'merged';
  return bucket.replace(/_/g, ' ');
}

function statusTone(status?: string): 'neutral' | 'sky' | 'green' | 'amber' | 'red' {
  if (status === 'auto_selected') return 'green';
  if (status === 'sent_to_llm') return 'sky';
  if (status === 'dropped_before_llm') return 'red';
  return 'neutral';
}

function statusLabel(status?: string) {
  if (status === 'auto_selected') return 'auto-selected';
  if (status === 'sent_to_llm') return 'sent to LLM';
  if (status === 'dropped_before_llm') return 'dropped before LLM';
  return null;
}

function statusReasonLabel(reason?: string) {
  if (reason === 'cross_confirmed_semantic_and_historical') return 'semantic + historic confirmed';
  if (reason === 'strong_historical_support') return 'strong historic support';
  if (reason === 'protected_historical_lane') return 'protected historic lane';
  if (reason === 'protected_confirmed_lane') return 'protected confirmed lane';
  if (reason === 'within_llm_candidate_cap') return 'inside LLM cap';
  if (reason === 'llm_candidate_cap') return 'cut by LLM cap';
  if (reason === 'insufficient_support') return 'insufficient support';
  return null;
}

function RetrievalPane({
  candidates,
  emptyText = 'No candidates retrieved yet.',
}: {
  candidates: Candidate[];
  emptyText?: string;
}) {
  if (!candidates.length) return <Empty text={emptyText} />;
  const best = Math.max(...candidates.map(c => scoreOf(c)), 0.0001);
  return (
    <div className="space-y-2">
      {candidates.map((c, i) => {
        const score = scoreOf(c);
        const pct = Math.max((score / best) * 100, 4);
        const rankingScore = Number(c.ranking_score ?? 0) || 0;
        const semanticScore = Number(c.semantic_score ?? 0) || 0;
        const historicalStrength = Number(c.historical_strength ?? 0) || 0;
        const supportCount = Number(c.support_count ?? c._support_count ?? 0) || 0;
        const historicalBest = Number(c.best_support_score ?? 0) || 0;
        const candidateStatus = String(c.candidate_status ?? '').trim();
        const candidateStatusReason = String(c.candidate_status_reason ?? '').trim();
        const note = typeof c.description === 'string' && c.description.trim()
          ? c.description.trim()
          : Array.isArray(c.historical_reasons) && c.historical_reasons.length
            ? String(c.historical_reasons[0] ?? '').trim()
            : '';
        const bucket = bucketLabel(c);
        return (
          <div key={i} className="rounded-md border border-zinc-200 bg-white px-3.5 py-3 transition hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900/50 dark:hover:border-zinc-700">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <span className="mr-2 text-xs font-mono text-zinc-400">#{i + 1}</span>
                <span className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{c.entity_name}</span>
              </div>
              <span className="shrink-0 text-xs font-mono text-hcsc dark:text-teal-300">{score.toFixed(4)}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {bucket && <Pill tone={bucketTone(String(c.bucket ?? c.candidate_source ?? ''))}>{bucket}</Pill>}
              {candidateStatus && <Pill tone={statusTone(candidateStatus)}>{statusLabel(candidateStatus)}</Pill>}
              {candidateStatusReason && <Pill tone="neutral">{statusReasonLabel(candidateStatusReason) ?? candidateStatusReason}</Pill>}
              {rankingScore > 0 && <Pill tone="green">rank {rankingScore.toFixed(3)}</Pill>}
              {semanticScore > 0 && <Pill tone="sky">semantic {semanticScore.toFixed(3)}</Pill>}
              {historicalStrength > 0 && <Pill tone="amber">hist strength {historicalStrength.toFixed(3)}</Pill>}
              {historicalBest > 0 && <Pill tone="amber">historic {historicalBest.toFixed(3)}</Pill>}
              {supportCount > 0 && <Pill tone="amber">{supportCount} hits</Pill>}
            </div>
            <div className="mt-2 h-1 rounded-full bg-stone-100 dark:bg-zinc-800">
              <div className="h-full rounded-full bg-hcsc transition-all dark:bg-teal-400" style={{ width: `${pct}%` }} />
            </div>
            {note && <p className="mt-2 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">{note}</p>}
          </div>
        );
      })}
    </div>
  );
}

function FaissHitsPane({
  hits,
  emptyText = 'No historical FAISS ticket hits were returned.',
}: {
  hits: HistoricalTicketHit[];
  emptyText?: string;
}) {
  if (!hits.length) return <Empty text={emptyText} />;
  const best = Math.max(...hits.map(hit => Number(hit.best_score ?? 0) || 0), 0.0001);
  return (
    <div className="space-y-2">
      {hits.map((hit, i) => {
        const score = Number(hit.best_score ?? 0) || 0;
        const pct = Math.max((score / best) * 100, 4);
        const direct = Array.isArray(hit.direct_vs_names) ? hit.direct_vs_names.filter(Boolean) : [];
        const implied = Array.isArray(hit.implied_vs_names) ? hit.implied_vs_names.filter(Boolean) : [];
        const names = Array.isArray(hit.value_stream_names) ? hit.value_stream_names.filter(Boolean) : [];
        const hasClassified = direct.length > 0 || implied.length > 0;
        const preview = String(hit.summary_preview ?? '').trim();
        return (
          <div key={`${hit.ticket_id}-${i}`} className="rounded-md border border-zinc-200 bg-white px-3.5 py-3 transition hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900/50 dark:hover:border-zinc-700">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-zinc-400">#{i + 1}</span>
                  <span className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">{hit.ticket_id}</span>
                </div>
                {hit.title && hit.title !== hit.ticket_id && (
                  <div className="mt-0.5 truncate text-xs text-zinc-500 dark:text-zinc-400">{hit.title}</div>
                )}
              </div>
              <span className="shrink-0 text-xs font-mono text-amber-700 dark:text-amber-300">{score.toFixed(4)}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Pill tone="amber">faiss</Pill>
              {hasClassified ? (
                <>
                  {direct.length > 0 && <Pill tone="green">{direct.length} direct</Pill>}
                  {implied.length > 0 && <Pill tone="amber">{implied.length} implied</Pill>}
                </>
              ) : (
                names.length > 0 && <Pill tone="neutral">{names.length} attached VS</Pill>
              )}
              {hit.label_source && <Pill tone="neutral">{String(hit.label_source)}</Pill>}
            </div>
            <div className="mt-2 h-1 rounded-full bg-stone-100 dark:bg-zinc-800">
              <div className="h-full rounded-full bg-amber-500 transition-all" style={{ width: `${pct}%` }} />
            </div>
            {preview && <p className="mt-2 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">{preview}</p>}
            {(direct.length > 0 || implied.length > 0 || names.length > 0) && (
              <div className="mt-3 space-y-2 border-t border-zinc-100 pt-3 dark:border-zinc-800">
                {direct.length > 0 && (
                  <div>
                    <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">Direct</div>
                    <div className="flex flex-wrap gap-1.5">
                      {direct.map(name => <Pill key={`d-${hit.ticket_id}-${name}`} tone="green">{name}</Pill>)}
                    </div>
                  </div>
                )}
                {implied.length > 0 && (
                  <div>
                    <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-amber-600 dark:text-amber-400">Implied</div>
                    <div className="flex flex-wrap gap-1.5">
                      {implied.map(name => <Pill key={`i-${hit.ticket_id}-${name}`} tone="amber">{name}</Pill>)}
                    </div>
                  </div>
                )}
                {!hasClassified && names.length > 0 && (
                  <div>
                    <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">Attached Streams</div>
                    <div className="flex flex-wrap gap-1.5">
                      {names.map(name => <Pill key={`n-${hit.ticket_id}-${name}`} tone="neutral">{name}</Pill>)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ComparisonPane({ selected, groundTruth, title }: { selected: SelectedVS[]; groundTruth?: string[]; title?: string }) {
  const gt = groundTruth ?? [];
  if (!selected.length && !gt.length) return <Empty text="Run the pipeline to compare against ground truth." />;

  const hasGroundTruth = gt.length > 0;
  const selNames = new Set(selected.map(s => s.entity_name.trim().toLowerCase()));
  const gtNames = new Set(gt.map(s => s.trim().toLowerCase()));
  const hasBackend = hasGroundTruth && selected.some(s => typeof s.is_match === 'boolean');

  let tp = hasGroundTruth ? selected.filter(s => gtNames.has(s.entity_name.trim().toLowerCase())) : [];
  let fp = hasGroundTruth ? selected.filter(s => !gtNames.has(s.entity_name.trim().toLowerCase())) : [];
  let fn = hasGroundTruth ? gt.filter(s => !selNames.has(s.trim().toLowerCase())) : [];

  if (hasBackend) {
    tp = selected.filter(s => s.is_match === true);
    fp = selected.filter(s => s.is_match !== true);
    const matched = new Set(selected.filter(s => s.is_match && s.matched_to_ground_truth).map(s => (s.matched_to_ground_truth ?? '').trim().toLowerCase()));
    fn = gt.filter(n => !matched.has(n.trim().toLowerCase()));
  }

  const precision = selected.length ? tp.length / selected.length : 0;
  const recall = gt.length ? tp.length / gt.length : 0;
  const f1 = precision + recall ? (2 * precision * recall) / (precision + recall) : 0;

  const metricColor = (v: number) => v >= 0.7 ? 'text-emerald-600 dark:text-emerald-400' : v >= 0.4 ? 'text-amber-600 dark:text-amber-400' : 'text-red-500';

  return (
    <div className="space-y-5">
      {gt.length > 0 && (
        <>
          {title && <p className="text-xs text-zinc-500 dark:text-zinc-400">Source: <span className="font-medium text-zinc-700 dark:text-zinc-200">{title}</span></p>}
          <div className="grid grid-cols-3 gap-3">
            {[
              { l: 'Precision', v: precision, d: 'Correct / AI selected' },
              { l: 'Recall', v: recall, d: 'Found / ground truth' },
              { l: 'F1', v: f1, d: 'Harmonic mean' },
            ].map(m => (
              <div key={m.l} className="rounded-md border border-zinc-200 bg-white p-3.5 text-center dark:border-zinc-800 dark:bg-zinc-900/50">
                <div className={`text-2xl font-extrabold ${metricColor(m.v)}`}>{Math.round(m.v * 100)}%</div>
                <div className="mt-0.5 text-xs font-semibold text-zinc-700 dark:text-zinc-200">{m.l}</div>
                <div className="text-[10px] text-zinc-500">{m.d}</div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* AI Predicted */}
        <div className="rounded-md border border-zinc-200 bg-white p-3.5 dark:border-zinc-800 dark:bg-zinc-900/50">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-bold text-zinc-700 dark:text-zinc-200 uppercase tracking-wide">AI Predicted</span>
            <Pill tone="sky">{selected.length}</Pill>
          </div>
          <div className="space-y-1.5">
            {selected.map((vs, i) => {
              const ok = hasGroundTruth && (hasBackend ? vs.is_match === true : gtNames.has(vs.entity_name.trim().toLowerCase()));
              return (
                <div key={i} className={`flex items-center justify-between rounded-lg border px-2.5 py-2 text-sm ${!hasGroundTruth ? 'border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800/40' : ok ? 'border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/30' : 'border-red-300 bg-red-50 dark:border-red-700 dark:bg-red-950/30'}`}>
                  <span className="truncate">{vs.entity_name}</span>
                  <span className={`shrink-0 ml-2 ${!hasGroundTruth ? 'text-zinc-400' : ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                    {!hasGroundTruth ? <span className="text-zinc-400">-</span> : ok ? <I n="check" className="w-3.5 h-3.5" /> : <I n="x" className="w-3.5 h-3.5" />}
                  </span>
                </div>
              );
            })}
            {!selected.length && <p className="text-xs text-zinc-400">None</p>}
          </div>
        </div>

        {/* Ground Truth */}
        <div className="rounded-md border border-zinc-200 bg-white p-3.5 dark:border-zinc-800 dark:bg-zinc-900/50">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-bold text-zinc-700 dark:text-zinc-200 uppercase tracking-wide">Ground Truth</span>
            <Pill tone="green">{gt.length}</Pill>
          </div>
          <div className="space-y-1.5">
            {gt.length === 0
              ? <p className="text-xs text-zinc-400 italic">No ground truth for this card.</p>
              : gt.map((name, i) => {
                  const ok = hasBackend
                    ? !fn.some(v => v.trim().toLowerCase() === name.trim().toLowerCase())
                    : selNames.has(name.trim().toLowerCase());
                  return (
                    <div key={i} className={`flex items-center justify-between rounded-lg border px-2.5 py-2 text-sm ${ok ? 'border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/30' : 'border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800/40'}`}>
                      <span className="truncate">{name}</span>
                      <span>{ok ? <I n="check" className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" /> : <span className="text-zinc-400">-</span>}</span>
                    </div>
                  );
                })
            }
          </div>
        </div>
      </div>

      {hasGroundTruth && (fp.length > 0 || fn.length > 0) && (
        <div className="grid grid-cols-2 gap-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
          <div>
            <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-red-500">False Positives ({fp.length})</div>
            {fp.map((v, i) => <div key={i} className="mt-1 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs dark:border-red-800 dark:bg-red-950/30">{v.entity_name}</div>)}
          </div>
          <div>
            <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-amber-600">Missed ({fn.length})</div>
            {fn.map((n, i) => <div key={i} className="mt-1 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs dark:border-amber-800 dark:bg-amber-950/30">{n}</div>)}
          </div>
        </div>
      )}
    </div>
  );
}


function ReferencePane({ canonical }: { canonical?: CanonicalVS[] }) {
  if (!canonical?.length) return <Empty text="No canonical value streams loaded." />;
  const grouped = canonical.reduce<Record<string, CanonicalVS[]>>((acc, vs) => {
    const cat = vs.category || 'other';
    (acc[cat] ??= []).push(vs);
    return acc;
  }, {});
  const catColors: Record<string, string> = {
    commercial: 'border-teal-200 bg-teal-50/70 dark:border-teal-800 dark:bg-teal-950/30',
    operational: 'border-teal-200 bg-teal-50/70 dark:border-teal-800 dark:bg-teal-950/30',
    financial: 'border-amber-200 bg-amber-50/70 dark:border-amber-800 dark:bg-amber-950/30',
    analytics: 'border-cyan-200 bg-cyan-50/70 dark:border-cyan-800 dark:bg-cyan-950/30',
  };
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {Object.entries(grouped).sort().map(([cat, items]) => (
        <div key={cat} className={`rounded-md border p-3.5 ${catColors[cat] || 'border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50'}`}>
          <div className="mb-2.5 text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">{cat}</div>
          <div className="space-y-1">
            {items.map(v => <div key={v.id} className="text-sm text-zinc-700 dark:text-zinc-200">{v.name}</div>)}
          </div>
        </div>
      ))}
    </div>
  );
}

function RawPane({ raw }: { raw?: unknown }) {
  if (raw == null) return <Empty text="No raw LLM output for this run." />;
  const rendered = typeof raw === 'string' ? raw : JSON.stringify(raw, null, 2);
  return (
    <pre className="idp-scroll max-h-[600px] overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-4 font-mono text-[11px] leading-relaxed text-zinc-700 whitespace-pre-wrap dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-200">
      {rendered}
    </pre>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> => Boolean(asRecord(row))) : [];
}

function rowsFromPass(pass: unknown, keys: string[]) {
  const record = asRecord(pass);
  if (!record) return [];
  for (const key of keys) {
    const rows = asRecordArray(record[key]);
    if (rows.length) return rows;
  }
  return [];
}

function textValue(value: unknown, fallback = '') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function LlmPassCard({ title, subtitle, raw }: { title: string; subtitle: string; raw: unknown }) {
  const selected = rowsFromPass(raw, ['selected_value_streams', 'selected', 'value_streams']);
  const rejected = rowsFromPass(raw, ['rejected_candidates', 'rejected']);

  return (
    <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3.5 dark:border-zinc-800 dark:bg-zinc-950/40">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">{title}</div>
          <div className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{subtitle}</div>
        </div>
        <div className="flex gap-1.5">
          <Pill tone="green">{selected.length} selected</Pill>
          <Pill tone="red">{rejected.length} rejected</Pill>
        </div>
      </div>

      {raw == null ? (
        <Empty text={`${title} output was not returned for this run.`} />
      ) : (
        <div className="space-y-3">
          <div>
            <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Selected</div>
            {selected.length ? (
              <div className="space-y-1.5">
                {selected.map((row, index) => {
                  const name = textValue(row.entity_name ?? row.value_stream_name ?? row.name, 'Unnamed value stream');
                  const confidence = Number(row.confidence ?? 0);
                  return (
                    <div key={`${name}-${index}`} className="rounded border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-800 dark:bg-emerald-950/30">
                      <div className="flex items-start justify-between gap-3">
                        <div className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{name}</div>
                        {confidence > 0 && <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-300">{Math.round(confidence * 100)}%</span>}
                      </div>
                      {textValue(row.reason) && <div className="mt-1 text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">{textValue(row.reason)}</div>}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">No selected rows in this pass.</div>
            )}
          </div>

          {rejected.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Rejected</div>
              <div className="space-y-1.5">
                {rejected.slice(0, 8).map((row, index) => {
                  const name = textValue(row.entity_name ?? row.value_stream_name ?? row.name, 'Unnamed value stream');
                  return (
                    <div key={`${name}-${index}`} className="rounded border border-zinc-200 bg-white px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900">
                      <div className="text-sm text-zinc-700 dark:text-zinc-200">{name}</div>
                      {textValue(row.reason) && <div className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{textValue(row.reason)}</div>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <details className="rounded border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-zinc-600 dark:text-zinc-300">Raw pass payload</summary>
            <RawPane raw={raw} />
          </details>
        </div>
      )}
    </div>
  );
}

function LlmPassesPane({ direct, historical, rawResponse }: { direct?: unknown; historical?: unknown; rawResponse?: unknown }) {
  const raw = asRecord(rawResponse);
  const directOutput = direct ?? raw?.direct_pass;
  const historicalOutput = historical ?? raw?.historical_gap_pass;

  if (directOutput == null && historicalOutput == null) {
    return <Empty text="No direct or historical LLM pass output was returned for this run." />;
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <LlmPassCard title="Direct LLM" subtitle="Semantic and confirmed candidate adjudication" raw={directOutput} />
      <LlmPassCard title="Historic LLM" subtitle="Historical gap and pattern-induced adjudication" raw={historicalOutput} />
    </div>
  );
}

// --- Tab button -----------------------------------------------------------

function TabBtn({ active, label, icon, count, onClick }: {
  active: boolean; label: string; icon: Ico; count?: number; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`relative flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-semibold transition ${
        active
          ? 'border-hcsc text-hcsc dark:border-teal-400 dark:text-teal-300'
          : 'border-transparent text-zinc-500 hover:border-zinc-300 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200'
      }`}
    >
      <I n={icon} className="w-3.5 h-3.5" />
      {label}
      {typeof count === 'number' && count > 0 && (
        <span className={`ml-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold ${active ? 'bg-teal-50 text-teal-900 dark:bg-teal-950/50 dark:text-teal-200' : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400'}`}>{count}</span>
      )}
    </button>
  );
}
// --- Main page ------------------------------------------------------------

export default function Home() {
  const [cards, setCards] = useState<IdeaCard[]>([]);
  const [selectedCardDocId, setSelectedCardDocId] = useState('');
  const [count, setCount] = useState(20);
  const [uploadedIdeaText, setUploadedIdeaText] = useState('');
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [excludeSourceTicketFromHistorical, setExcludeSourceTicketFromHistorical] = useState(true);
  const [dark, setDark] = useState(true);

  const [step, setStep] = useState<Step>('idle');
  const [stepLabel, setStepLabel] = useState('');
  const [stepOnError, setStepOnError] = useState<Step>('idle');
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [tab, setTab] = useState<Tab>('selection');

  const currentPipelineStepRef = useRef<Step>('idle');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load cards + theme
  useEffect(() => {
    const savedTheme = localStorage.getItem('vs-theme');
    if (savedTheme) setDark(savedTheme === 'dark');
    fetch(`${API}/api/idea-cards`)
      .then(r => r.json())
      .then(d => { setCards(d.cards ?? []); if (d.cards?.length) setSelectedCardDocId(d.cards[0].doc_id); })
      .catch(() => {});
  }, []);

  const maxCandidateCount = 50;

  const cands = useMemo(() => result?.candidate_value_streams ?? result?.candidates_used ?? [], [result]);
  const vsCandidates = useMemo(() => result?.semantic_candidate_value_streams ?? [], [result]);
  const historicCandidates = useMemo(() => result?.historical_candidate_value_streams ?? [], [result]);
  const faissHits = useMemo(() => result?.historical_ticket_hits ?? [], [result]);
  const mergedCandidates = useMemo(
    () => result?.merged_candidate_value_streams ?? result?.candidate_value_streams ?? result?.candidates_used ?? [],
    [result],
  );
  const hasHistoricCandidateTabs = useMemo(
    () => Boolean(
      result
      && (
        result.semantic_candidate_value_streams !== undefined
        || result.historical_candidate_value_streams !== undefined
        || result.historical_ticket_hits !== undefined
        || result.merged_candidate_value_streams !== undefined
      )
    ),
    [result],
  );
  const resultCandidateCount = hasHistoricCandidateTabs ? mergedCandidates.length : cands.length;
  const selectedCard = cards.find(c => c.doc_id === selectedCardDocId);
  const selectedTicketId = useMemo(() => {
    const value = String(selectedCard?.doc_id ?? '').trim().toUpperCase();
    return TICKET_KEY_RE.test(value) ? value : '';
  }, [selectedCard]);
  const canRun = Boolean(uploadedIdeaText.trim() || selectedTicketId);

  const extractDroppedIdeaCard = useCallback(async (file: File) => {
    setUploadError(null);
    setUploading(true);
    try {
      const res = await fetch(`${API}/api/idea-cards/extract?filename=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: await file.arrayBuffer(),
      });
      if (!res.ok) {
        const failure = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(failure.detail ?? 'Could not extract idea card.');
      }
      const payload = await res.json() as { filename?: string; text?: string; char_count?: number };
      if (!payload.text?.trim()) throw new Error('No text could be extracted from this idea card.');
      setUploadedIdeaText(payload.text);
      setUploadedFileName(payload.filename || file.name);
      setResult(null);
      setErr(null);
      setStep('idle');
      setStepLabel('');
    } catch (error: unknown) {
      setUploadedIdeaText('');
      setUploadedFileName('');
      setUploadError(error instanceof Error ? error.message : String(error));
    } finally {
      setUploading(false);
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const file = event.dataTransfer.files?.[0];
    if (file) void extractDroppedIdeaCard(file);
    else setDragActive(false);
  }, [extractDroppedIdeaCard]);

  const handleFileInput = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void extractDroppedIdeaCard(file);
    event.target.value = '';
  }, [extractDroppedIdeaCard]);

  const clearUpload = useCallback(() => {
    setUploadedIdeaText('');
    setUploadedFileName('');
    setUploadError(null);
  }, []);

  const toggleTheme = useCallback(() => {
    setDark(current => {
      const next = !current;
      localStorage.setItem('vs-theme', next ? 'dark' : 'light');
      return next;
    });
  }, []);

  const run = useCallback(async () => {
    setErr(null); setResult(null); setStepLabel(''); setStep('idle');
    currentPipelineStepRef.current = 'idle';

    try {
      const body: Record<string, unknown> = {
        top_k_value_streams: count,
        top_k_historical: count,
        use_llm_finalizer: true,
        exclude_source_ticket_from_historical: excludeSourceTicketFromHistorical,
      };
      if (uploadedIdeaText.trim()) {
        body.idea_card_text = uploadedIdeaText.trim();
        if (selectedTicketId) body.ticket_id = selectedTicketId;
      } else if (selectedTicketId) body.ticket_id = selectedTicketId;
      else throw new Error('Select a Jira idea card or drop an extractable idea-card file.');

      const res = await fetch(`${API}/rag/value-streams/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail ?? 'Failed');
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';

        for (const part of parts) {
          if (!part.trim()) continue;
          const lines = part.split('\n');
          let eventType = '';
          let data = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim();
            if (line.startsWith('data: ')) data = line.slice(6);
          }

          if (eventType === 'step') {
            const payload = JSON.parse(data) as { step: Step; label: string };
            currentPipelineStepRef.current = payload.step;
            setStep(payload.step);
            setStepLabel(payload.label || '');
          } else if (eventType === 'result') {
            const payload = JSON.parse(data) as PipelineResult;
            setResult(payload);
            setStepLabel('');
            setStep('done');
            setTab('selection');
          } else if (eventType === 'error') {
            const payload = JSON.parse(data) as { message: string };
            setStepOnError(currentPipelineStepRef.current);
            setStep('error');
            setErr(payload.message);
            return;
          }
        }
      }
    } catch (e: unknown) {
      setStepOnError(currentPipelineStepRef.current);
      setStep('error');
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [count, excludeSourceTicketFromHistorical, selectedTicketId, uploadedIdeaText]);

  const busy = step !== 'idle' && step !== 'done' && step !== 'error';
  const card = selectedCard;

  return (
    <div className={`${dark ? 'dark' : ''} min-h-screen bg-surface-muted text-zinc-900 dark:text-zinc-100`}>
      <header className="border-b border-zinc-200 bg-white/95 px-6 py-3.5 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95">
        <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-hcsc text-white shadow-sm dark:bg-teal-700">
              <I n="layers" className="w-4 h-4" />
            </span>
            <div>
              <h1 className="text-[17px] font-semibold leading-tight tracking-tight text-zinc-950 dark:text-zinc-50">Value Stream Explorer</h1>
              <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">Idea-card value stream retrieval</p>
            </div>
          </div>
          <button
            onClick={toggleTheme}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-semibold text-zinc-700 shadow-sm transition hover:border-teal-400 hover:text-teal-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:border-teal-500 dark:hover:text-teal-300"
            aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            <I n={dark ? 'sun' : 'moon'} className="w-3.5 h-3.5" />
            {dark ? 'Light' : 'Dark'}
          </button>
        </div>
      </header>
      <div className="mx-auto max-w-[1500px] px-6 py-5">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">

          {/* Sidebar ------------------------------------------------------- */}
          <aside className="space-y-4 xl:sticky xl:top-5 self-start">
            <Section title="Input" icon="file">
              <div className="space-y-3">
                <select
                  value={selectedCardDocId}
                  onChange={e => {
                    setSelectedCardDocId(e.target.value);
                    clearUpload();
                  }}
                  className="h-11 w-full rounded-lg border border-zinc-300 bg-white px-3 text-sm font-medium text-zinc-800 outline-none transition focus:border-hcsc focus:ring-2 focus:ring-teal-400/25 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                >
                  {cards.map(c => (
                    <option key={c.doc_id} value={c.doc_id}>{c.doc_id} - {c.display_name}</option>
                  ))}
                </select>

                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".ppt,.pptx,.pdf,.doc,.docx,.txt,.md,.markdown"
                  onChange={handleFileInput}
                  className="hidden"
                />
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => fileInputRef.current?.click()}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') fileInputRef.current?.click();
                  }}
                  onDragEnter={event => {
                    event.preventDefault();
                    setDragActive(true);
                  }}
                  onDragOver={event => {
                    event.preventDefault();
                    setDragActive(true);
                  }}
                  onDragLeave={event => {
                    event.preventDefault();
                    setDragActive(false);
                  }}
                  onDrop={handleDrop}
                  className={`group cursor-pointer rounded-xl border p-5 text-center transition ${
                    dragActive
                      ? 'border-teal-400 bg-teal-50 text-teal-950 shadow-[0_0_0_4px_rgba(45,212,191,0.15)] dark:bg-teal-950/40 dark:text-teal-100'
                      : 'border-teal-200 bg-gradient-to-b from-teal-50/70 to-white text-zinc-800 hover:border-teal-400 hover:shadow-[0_12px_28px_rgba(15,118,110,0.10)] dark:border-zinc-700 dark:from-zinc-900 dark:to-zinc-950 dark:text-zinc-200 dark:hover:border-teal-500 dark:hover:bg-teal-950/20'
                  }`}
                >
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-teal-200 bg-white text-teal-700 shadow-sm transition group-hover:border-teal-400 dark:border-teal-800 dark:bg-teal-950/40 dark:text-teal-300">
                    <I n={uploading ? 'loader' : 'file'} className="w-5 h-5" />
                  </div>
                  <div className="text-[15px] font-semibold tracking-tight">
                    {uploading ? 'Extracting idea card...' : uploadedFileName || 'Drop idea card here'}
                  </div>
                  <div className="mt-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">
                    PPTX, PDF, DOCX, TXT, or Markdown
                  </div>
                  <div className="mt-3 inline-flex rounded-md border border-teal-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-teal-800 shadow-sm dark:border-teal-800 dark:bg-zinc-900 dark:text-teal-300">
                    Browse file
                  </div>
                </div>

                {uploadError && (
                  <div className="rounded-md border border-rose-800 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
                    {uploadError}
                  </div>
                )}

                {uploadedFileName ? (
                  <div className="rounded-lg border border-teal-200 bg-teal-50 p-3 text-xs dark:border-teal-800 dark:bg-teal-950/30">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="font-semibold text-teal-900 dark:text-teal-200">{uploadedFileName}</div>
                        <div className="mt-1 text-zinc-500 dark:text-zinc-400">{uploadedIdeaText.length.toLocaleString()} extracted characters</div>
                      </div>
                      <button onClick={clearUpload} className="rounded-md border border-zinc-300 bg-white px-2 py-1 font-semibold text-zinc-600 hover:border-teal-400 hover:text-teal-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800">
                        Clear
                      </button>
                    </div>
                  </div>
                ) : card && (
                  <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-950/50">
                    <div className="text-zinc-600 dark:text-zinc-400">{card.display_name}</div>
                    {selectedTicketId ? (
                      <div className="mt-1 text-hcsc dark:text-teal-300">
                        Ticket key: {selectedTicketId}
                      </div>
                    ) : (
                      <div className="mt-1 text-amber-600 dark:text-amber-400">
                        Local idea-card doc_id only. Drop the file to run it.
                      </div>
                    )}
                    {card.has_mapping && (
                      <div className="mt-1 flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                        <I n="check" className="w-3 h-3" /> Ground truth available
                      </div>
                    )}
                  </div>
                )}
              </div>
            </Section>

            <Section title="Config" icon="sliders">
              <div className="space-y-4">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Historical RAG Candidates</span>
                    <Pill tone="sky">{count}</Pill>
                  </div>
                  <input type="range" min={5} max={maxCandidateCount} value={count} onChange={e => setCount(+e.target.value)} className="w-full accent-hcsc dark:accent-teal-400" />
                  <p className="mt-2 text-xs leading-5 text-zinc-500 dark:text-zinc-400">
                    Historical RAG can surface up to 50 value-stream candidates before the merge.
                  </p>
                </div>
                <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
                  <span className="min-w-0">
                    <span className="block text-xs font-semibold text-zinc-700 dark:text-zinc-200">Exclude source ticket</span>
                    <span className="block truncate text-[11px] text-zinc-500 dark:text-zinc-400">
                      {selectedTicketId ? selectedTicketId : 'No ticket key selected'}
                    </span>
                  </span>
                  <input
                    type="checkbox"
                    checked={excludeSourceTicketFromHistorical}
                    onChange={e => setExcludeSourceTicketFromHistorical(e.target.checked)}
                    className="h-4 w-4 rounded border-zinc-300 text-hcsc accent-hcsc focus:ring-hcsc dark:border-zinc-700 dark:accent-teal-400"
                  />
                </label>
                <button
                  onClick={run}
                  disabled={busy || uploading || !canRun}
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-hcsc px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-hcsc-hover disabled:cursor-not-allowed disabled:bg-zinc-300 disabled:text-white disabled:shadow-none dark:bg-teal-700 dark:hover:bg-teal-600 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
                >
                  {busy
                    ? <><I n="loader" className="w-4 h-4" /> Running...</>
                    : <><I n="play" className="w-4 h-4" /> Run Historical RAG</>
                  }
                </button>
              </div>
            </Section>
          </aside>

          {/* Main content -------------------------------------------------- */}
          <main className="min-w-0 space-y-4">

            {/* Pipeline flow graph */}
            <Section
              title="Pipeline"
              icon="layers"
              noPad
              badge={<Pill tone={step === 'done' ? 'green' : step === 'error' ? 'red' : step === 'idle' ? 'neutral' : 'sky'}>{step}</Pill>}
            >
              <PipelineGraph step={step} stepLabel={stepLabel} stepOnError={stepOnError} />
            </Section>

            {/* Error */}
            {err && (
              <div className="flex items-start gap-3 rounded-md border border-red-300 bg-red-50 px-4 py-3 dark:border-red-800 dark:bg-red-950/30">
                <I n="alert" className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <div className="text-sm font-semibold text-red-700 dark:text-red-300">Error</div>
                  <div className="mt-0.5 text-xs font-mono text-red-600 dark:text-red-400">{err}</div>
                </div>
              </div>
            )}

            {/* Idle */}
            {step === 'idle' && !result && (
              <div className="flex min-h-[360px] flex-col items-center justify-center rounded-xl border border-zinc-200 bg-white px-6 py-16 text-center shadow-[0_10px_30px_rgba(15,23,42,0.04)] dark:border-zinc-800 dark:bg-zinc-900/60 dark:shadow-none">
                <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-xl border border-teal-200 bg-teal-50 text-hcsc shadow-sm dark:border-teal-800 dark:bg-teal-950/40 dark:text-teal-400">
                  <I n="rocket" className="w-5 h-5" />
                </div>
                <h3 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Ready</h3>
                <p className="mt-2 max-w-md text-sm leading-6 text-zinc-500 dark:text-zinc-400">
                  Select an idea card and run historical RAG to retrieve candidates, merge historical support, and predict value streams.
                </p>
              </div>
            )}

            {/* Results */}
            {result && (
              <Section
                title="Results"
                icon="award"
                badge={
                  <div className="flex items-center gap-2">
                    <Pill tone="neutral">{resultCandidateCount} candidates</Pill>
                    <Pill tone="green">{result.selected_value_streams?.length ?? 0} selected</Pill>
                  </div>
                }
              >
                <div className="mb-4 flex flex-wrap gap-2">
                  <TabBtn active={tab === 'selection'}  label="Selection"  icon="zap"       count={result.selected_value_streams?.length}  onClick={() => setTab('selection')} />
                  <TabBtn active={tab === 'comparison'} label="Comparison" icon="scale"     count={result.ground_truth?.length}            onClick={() => setTab('comparison')} />
                  <TabBtn active={tab === 'llmPasses'}  label="LLM Passes" icon="cpu"                                                    onClick={() => setTab('llmPasses')} />
                  {hasHistoricCandidateTabs ? (
                    <>
                      <TabBtn active={tab === 'vsCandidates'}       label="VS Candidates" icon="database" count={vsCandidates.length}       onClick={() => setTab('vsCandidates')} />
                      <TabBtn active={tab === 'historicCandidates'} label="Historic"      icon="book"     count={historicCandidates.length} onClick={() => setTab('historicCandidates')} />
                      <TabBtn active={tab === 'faissHits'}          label="FAISS Hits"    icon="file"     count={faissHits.length}         onClick={() => setTab('faissHits')} />
                      <TabBtn active={tab === 'mergedCandidates'}   label="Merged"        icon="layers"   count={mergedCandidates.length}   onClick={() => setTab('mergedCandidates')} />
                    </>
                  ) : (
                    <TabBtn active={tab === 'retrieval'} label="Retrieval" icon="database" count={cands.length} onClick={() => setTab('retrieval')} />
                  )}
                  <TabBtn active={tab === 'reference'}  label="Reference"  icon="book"      count={result.canonical_value_streams?.length} onClick={() => setTab('reference')} />
                  <TabBtn active={tab === 'raw'}        label="Raw LLM"    icon="terminal"                                                 onClick={() => setTab('raw')} />
                </div>

                {tab === 'selection'  && <SelectionPane  selected={result.selected_value_streams ?? []} rejected={result.rejected_candidates ?? []} />}
                {tab === 'comparison' && <ComparisonPane selected={result.selected_value_streams ?? []} groundTruth={result.ground_truth} title={result.ground_truth_title} />}
                {tab === 'llmPasses'  && <LlmPassesPane direct={result.direct_llm_output} historical={result.historical_llm_output} rawResponse={result.raw_response} />}
                {tab === 'retrieval'  && <RetrievalPane  candidates={cands} />}
                {tab === 'vsCandidates'       && <RetrievalPane candidates={vsCandidates} emptyText="No semantic VS candidates retrieved yet." />}
                {tab === 'historicCandidates' && <RetrievalPane candidates={historicCandidates} emptyText="No historic candidates recovered yet." />}
                {tab === 'faissHits'          && <FaissHitsPane hits={faissHits} emptyText="No IDMT ticket hits recovered from FAISS yet." />}
                {tab === 'mergedCandidates'   && <RetrievalPane candidates={mergedCandidates} emptyText="No merged candidates available yet." />}
                {tab === 'reference'  && <ReferencePane  canonical={result.canonical_value_streams} />}
                {tab === 'raw'        && <RawPane        raw={result.raw_response} />}
              </Section>
            )}

          </main>
        </div>
      </div>
    </div>
  );
}
