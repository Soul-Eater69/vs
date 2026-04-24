'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
type Step = 'idle' | 'extracting' | 'retrieving' | 'generating' | 'done' | 'error';
type Tab =
  | 'selection'
  | 'comparison'
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
         | 'chevDown';

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
    default:          return <svg {...p}><circle cx="12" cy="12" r="1" /></svg>;
  }
}

// --- UI primitives --------------------------------------------------------

function Pill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'sky' | 'green' | 'amber' | 'red' }) {
  const cls: Record<string, string> = {
    neutral: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300',
    sky:     'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300',
    green:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
    amber:   'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    red:     'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${cls[tone]}`}>{children}</span>;
}

function Section({ title, icon, badge, children, noPad }: {
  title: string; icon: Ico; badge?: React.ReactNode; children: React.ReactNode; noPad?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center justify-between gap-3 border-b border-zinc-100 px-5 py-3 dark:border-zinc-800">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-800 dark:text-zinc-100">
          <span className="text-sky-600 dark:text-sky-400"><I n={icon} className="w-4 h-4" /></span>
          {title}
        </div>
        {badge}
      </div>
      <div className={noPad ? '' : 'p-5'}>{children}</div>
    </div>
  );
}

// --- React Flow pipeline --------------------------------------------------

type StepNodeData = { title: string; subtitle: string; icon: Ico; status: 'idle' | 'active' | 'done' | 'error' };

function StepNode({ data }: NodeProps<Node<StepNodeData>>) {
  const base = 'w-[180px] rounded-xl border px-3.5 py-3 shadow-sm transition-all duration-300';
  const v: Record<string, string> = {
    idle:   'border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-400',
    active: 'border-sky-400 bg-sky-50 text-sky-700 shadow-sky-200/50 dark:border-sky-500 dark:bg-sky-950/40 dark:text-sky-300 dark:shadow-sky-900/30',
    done:   'border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-500 dark:bg-emerald-950/40 dark:text-emerald-300',
    error:  'border-red-400 bg-red-50 text-red-700 dark:border-red-500 dark:bg-red-950/40 dark:text-red-300',
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
  { id: 'extract',  x: 0,   icon: 'file' as Ico,     title: 'Extract',   subtitle: 'Clean & summarize text' },
  { id: 'retrieve', x: 240, icon: 'database' as Ico, title: 'Retrieve',  subtitle: 'VS index + historical support' },
  { id: 'select',   x: 480, icon: 'cpu' as Ico,      title: 'LLM Select', subtitle: 'GPT picks value streams' },
  { id: 'finalize', x: 720, icon: 'award' as Ico,    title: 'Finalize',  subtitle: 'De-dup & output' },
];

const FLOW_EDGES_BASE: Edge[] = [
  { id: 'e1', source: 'extract',  target: 'retrieve', markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e2', source: 'retrieve', target: 'select',   markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
  { id: 'e3', source: 'select',   target: 'finalize', markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 } },
];

function PipelineGraph({ step }: { step: Step }) {
  const seq: Step[] = ['extracting', 'retrieving', 'generating', 'done'];
  const idx = step === 'error' ? 2 : seq.indexOf(step);

  const nodes = useMemo<Node<StepNodeData>[]>(() =>
    STEP_DEFS.map((d, i) => ({
      id: d.id,
      type: 'step' as const,
      position: { x: d.x, y: 30 },
      draggable: false,
      selectable: false,
      data: {
        title: d.title,
        subtitle: d.subtitle,
        icon: d.icon,
        status: step === 'error' && i >= 2 ? 'error' : i < idx ? 'done' : i === idx ? 'active' : 'idle',
      },
    })),
  [step, idx]);

  const edges = useMemo<Edge[]>(() =>
    FLOW_EDGES_BASE.map((e, i) => ({
      ...e,
      animated: i < idx,
      style: { strokeWidth: 2, stroke: i < idx ? '#10b981' : '#a1a1aa' },
    })),
  [idx]);

  return (
    <div className="h-[180px] w-full">
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
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-zinc-100 text-zinc-400 dark:bg-zinc-800 dark:text-zinc-500">
        <I n="search" className="w-4 h-4" />
      </div>
      <p className="text-sm text-zinc-500 dark:text-zinc-400 max-w-xs">{text}</p>
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
            <div key={i} className="rounded-xl border border-emerald-200 bg-emerald-50/80 p-3.5 dark:border-emerald-800 dark:bg-emerald-950/30">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">{vs.entity_name}</div>
                  <div className="mt-0.5 text-[11px] font-mono text-zinc-400 dark:text-zinc-500">{vs.entity_id}</div>
                </div>
                <span className={`text-xs font-bold ${pct >= 80 ? 'text-emerald-600 dark:text-emerald-400' : pct >= 50 ?
                  'text-amber-600 dark:text-amber-400' : 'text-red-500'}`}>{pct}%</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-emerald-200/60 dark:bg-emerald-900/40">
                <div className="h-full rounded-full bg-emerald-500 transition-all duration-700" style={{ width: `${pct}%` }} />
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
              <div key={i} className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/50">
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
        const semanticScore = Number(c.semantic_score ?? 0) || 0;
        const supportCount = Number(c.support_count ?? c._support_count ?? 0) || 0;
        const historicalBest = Number(c.best_support_score ?? 0) || 0;
        const note = typeof c.description === 'string' && c.description.trim()
          ? c.description.trim()
          : Array.isArray(c.historical_reasons) && c.historical_reasons.length
            ? String(c.historical_reasons[0] ?? '').trim()
            : '';
        const bucket = bucketLabel(c);
        return (
          <div key={i} className="rounded-xl border border-zinc-200 bg-white px-3.5 py-3 dark:border-zinc-800 dark:bg-zinc-900/50">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <span className="mr-2 text-xs font-mono text-zinc-400">#{i + 1}</span>
                <span className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{c.entity_name}</span>
              </div>
              <span className="shrink-0 text-xs font-mono text-sky-600 dark:text-sky-400">{score.toFixed(4)}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {bucket && <Pill tone={bucketTone(String(c.bucket ?? c.candidate_source ?? ''))}>{bucket}</Pill>}
              {semanticScore > 0 && <Pill tone="sky">semantic {semanticScore.toFixed(3)}</Pill>}
              {historicalBest > 0 && <Pill tone="amber">historic {historicalBest.toFixed(3)}</Pill>}
              {supportCount > 0 && <Pill tone="amber">{supportCount} hits</Pill>}
            </div>
            <div className="mt-2 h-1 rounded-full bg-zinc-100 dark:bg-zinc-800">
              <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${pct}%` }} />
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
          <div key={`${hit.ticket_id}-${i}`} className="rounded-xl border border-zinc-200 bg-white px-3.5 py-3 dark:border-zinc-800 dark:bg-zinc-900/50">
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
              <span className="shrink-0 text-xs font-mono text-amber-600 dark:text-amber-400">{score.toFixed(4)}</span>
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
            <div className="mt-2 h-1 rounded-full bg-zinc-100 dark:bg-zinc-800">
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
              <div key={m.l} className="rounded-xl border border-zinc-200 bg-zinc-50 p-3.5 text-center dark:border-zinc-800 dark:bg-zinc-900/50">
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
        <div className="rounded-xl border border-zinc-200 bg-white p-3.5 dark:border-zinc-800 dark:bg-zinc-900/50">
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
        <div className="rounded-xl border border-zinc-200 bg-white p-3.5 dark:border-zinc-800 dark:bg-zinc-900/50">
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
    commercial: 'border-sky-200 bg-sky-50/70 dark:border-sky-800 dark:bg-sky-950/30',
    operational: 'border-teal-200 bg-teal-50/70 dark:border-teal-800 dark:bg-teal-950/30',
    financial: 'border-amber-200 bg-amber-50/70 dark:border-amber-800 dark:bg-amber-950/30',
    analytics: 'border-cyan-200 bg-cyan-50/70 dark:border-cyan-800 dark:bg-cyan-950/30',
  };
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {Object.entries(grouped).sort().map(([cat, items]) => (
        <div key={cat} className={`rounded-xl border p-3.5 ${catColors[cat] || 'border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50'}`}>
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
    <pre className="max-h-[600px] overflow-auto rounded-xl border border-zinc-200 bg-zinc-50 p-4 text-[11px] leading-relaxed text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-200 font-mono whitespace-pre-wrap">
      {rendered}
    </pre>
  );
}

// --- Tab button -----------------------------------------------------------

function TabBtn({ active, label, icon, count, onClick }: {
  active: boolean; label: string; icon: Ico; count?: number; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-all ${
        active
          ? 'border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300'
          : 'border-transparent text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800/50'
      }`}
    >
      <I n={icon} className="w-3.5 h-3.5" />
      {label}
      {typeof count === 'number' && count > 0 && (
        <Pill tone={active ? 'sky' : 'neutral'}>{count}</Pill>
      )}
    </button>
  );
}

// --- Main page ------------------------------------------------------------

export default function Home() {
  const [cards, setCards] = useState<IdeaCard[]>([]);
  const [selectedCardDocId, setSelectedCardDocId] = useState('');
  const [query, setQuery] = useState('');
  const [custom, setCustom] = useState(false);
  const [count, setCount] = useState(20);
  const [dark, setDark] = useState(true);

  const [step, setStep] = useState<Step>('idle');
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [tab, setTab] = useState<Tab>('selection');

  const timer = useRef<ReturnType<typeof setTimeout>>(null);

  // Load cards + theme
  useEffect(() => {
    const saved = localStorage.getItem('vs-theme');
    if (saved) setDark(saved === 'dark');
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

  const toggle = useCallback(() => {
    setDark(d => { const n = !d; localStorage.setItem('vs-theme', n ? 'dark' : 'light'); return n; });
  }, []);

  const run = useCallback(async () => {
    setErr(null); setResult(null); setStep('extracting');
    timer.current = setTimeout(() => setStep('retrieving'), 1500);

    try {
      const body: Record<string, unknown> = {
        top_k_value_streams: count,
        top_k_historical: count,
        use_llm_finalizer: true,
      };
      if (custom && query.trim()) body.idea_card_text = query.trim();
      else if (selectedTicketId) body.ticket_id = selectedTicketId;
      else throw new Error('Selected card is a local doc_id, not a real ticket key. Use Custom Text for this card.');

      const res = await fetch(`${API}/rag/value-streams`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (timer.current) clearTimeout(timer.current);
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail ?? 'Failed');
      }

      const data: PipelineResult = await res.json();
      setStep('generating');
      await new Promise(r => setTimeout(r, 400));
      setStep('done');
      setResult(data);
      setTab('selection');
    } catch (e: unknown) {
      if (timer.current) clearTimeout(timer.current);
      setStep('error');
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [count, custom, query, selectedTicketId]);

  const busy = step !== 'idle' && step !== 'done' && step !== 'error';
  const card = selectedCard;

  return (
    <div className={`${dark ? 'dark' : ''} min-h-screen bg-zinc-100 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100`}>
      <div className="mx-auto max-w-[1440px] px-5 py-5">

        {/* Header */}
        <header className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-100 text-sky-600 dark:bg-sky-900/40 dark:text-sky-400">
              <I n="zap" className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight">Value Stream Explorer</h1>
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400">HCSC IDP • Idea-card analysis dashboard</p>
            </div>
          </div>
          <button
            onClick={toggle}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 shadow-sm hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <I n={dark ? 'sun' : 'moon'} className="w-3.5 h-3.5" />
            {dark ? 'Light' : 'Dark'}
          </button>
        </header>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">

          {/* Sidebar ------------------------------------------------------- */}
          <aside className="space-y-4 xl:sticky xl:top-5 self-start">
            <Section title="Input" icon="file">
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-1 rounded-lg border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-700 dark:bg-zinc-800">
                  {[false, true].map(isCustom => (
                    <button
                      key={String(isCustom)}
                      onClick={() => setCustom(isCustom)}
                      className={`rounded-md py-1.5 text-xs font-semibold transition ${custom === isCustom ?
                        'bg-sky-500 text-white shadow-sm' : 'text-zinc-600 dark:text-zinc-300'}`}
                    >
                      {isCustom ? 'Custom Text' : 'Idea Card'}
                    </button>
                  ))}
                </div>

                {!custom ? (
                  <>
                    <select
                      value={selectedCardDocId}
                      onChange={e => setSelectedCardDocId(e.target.value)}
                      className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-sky-400/40 dark:border-zinc-700 dark:bg-zinc-900"
                    >
                      {cards.map(c => (
                        <option key={c.doc_id} value={c.doc_id}>{c.doc_id} - {c.display_name}</option>
                      ))}
                    </select>
                    {card && (
                      <div className="rounded-lg border border-zinc-100 bg-zinc-50 p-2.5 text-xs dark:border-zinc-800 dark:bg-zinc-900/50">
                        <div className="text-zinc-600 dark:text-zinc-400">{card.display_name}</div>
                        {selectedTicketId ? (
                          <div className="mt-1 text-sky-600 dark:text-sky-400">
                            Ticket key: {selectedTicketId}
                          </div>
                        ) : (
                          <div className="mt-1 text-amber-600 dark:text-amber-400">
                            Local idea-card doc_id only. Use Custom Text to run historical RAG for this card.
                          </div>
                        )}
                        {card.has_mapping && (
                          <div className="mt-1 flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                            <I n="check" className="w-3 h-3" /> Ground truth available
                          </div>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <textarea
                    rows={6}
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    placeholder="Paste idea card text..."
                    className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-sky-400/40 dark:border-zinc-700 dark:bg-zinc-900 resize-none"
                  />
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
                  <input type="range" min={5} max={maxCandidateCount} value={count} onChange={e => setCount(+e.target.value)} className="w-full accent-sky-500" />
                  <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                    Historical RAG can surface up to 50 value-stream candidates before the merge.
                  </p>
                </div>
                <button
                  onClick={run}
                  disabled={busy || (!custom && !selectedCardDocId) || (!custom && !selectedTicketId) || (custom && !query.trim())}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-sky-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-sky-600/20 transition hover:bg-sky-500 active:bg-sky-700 disabled:opacity-40 disabled:cursor-not-allowed"
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
              <PipelineGraph step={step} />
            </Section>

            {/* Error */}
            {err && (
              <div className="flex items-start gap-3 rounded-xl border border-red-300 bg-red-50 px-4 py-3 dark:border-red-800 dark:bg-red-950/30">
                <I n="alert" className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <div className="text-sm font-semibold text-red-700 dark:text-red-300">Error</div>
                  <div className="mt-0.5 text-xs font-mono text-red-600 dark:text-red-400">{err}</div>
                </div>
              </div>
            )}

            {/* Idle */}
            {step === 'idle' && !result && (
              <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-zinc-300 bg-white/60 py-20 text-center dark:border-zinc-700 dark:bg-zinc-900/30">
                <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full border border-sky-200 bg-sky-50 text-sky-600 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-400">
                  <I n="rocket" className="w-6 h-6" />
                </div>
                <h3 className="text-base font-semibold text-zinc-700 dark:text-zinc-200">Ready</h3>
                <p className="mt-1 max-w-sm text-sm text-zinc-500 dark:text-zinc-400">
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
                <div className="mb-4 flex flex-wrap gap-1.5">
                  <TabBtn active={tab === 'selection'}  label="Selection"  icon="zap"       count={result.selected_value_streams?.length}  onClick={() => setTab('selection')} />
                  <TabBtn active={tab === 'comparison'} label="Comparison" icon="scale"     count={result.ground_truth?.length}            onClick={() => setTab('comparison')} />
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
