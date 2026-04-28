'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

const API = 'http://localhost:8000';
const TICKET_KEY_RE = /^[A-Z][A-Z0-9_]*-\d+$/;

type Step = 'idle' | 'extract' | 'condense' | 'semantic' | 'historical' | 'finalize' | 'done' | 'error';
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

interface RejectedVS {
  entity_id: string;
  entity_name: string;
  reason: string;
}

interface CanonicalVS {
  id: string;
  name: string;
  category: string;
}

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

type IconName =
  | 'file'
  | 'bolt'
  | 'search'
  | 'database'
  | 'spark'
  | 'upload'
  | 'play'
  | 'refresh'
  | 'download'
  | 'dots'
  | 'chevronDown'
  | 'chevronRight'
  | 'check'
  | 'circle'
  | 'filter'
  | 'copy'
  | 'list'
  | 'grid'
  | 'edit'
  | 'link'
  | 'clock'
  | 'users'
  | 'badge'
  | 'sliders'
  | 'menu';

function Icon({ name, className = 'h-4 w-4' }: { name: IconName; className?: string }) {
  const p = {
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
  };

  switch (name) {
    case 'file':
      return <svg {...p}><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z" /><path d="M14 2v5h5" /></svg>;
    case 'bolt':
      return <svg {...p}><path d="M13 2 3 14h7l-1 8 10-12h-7z" /></svg>;
    case 'search':
      return <svg {...p}><circle cx="11" cy="11" r="7.5" /><path d="m20 20-3.5-3.5" /></svg>;
    case 'database':
      return <svg {...p}><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></svg>;
    case 'spark':
      return <svg {...p}><path d="M12 3v4" /><path d="M12 17v4" /><path d="M4.9 4.9 7.7 7.7" /><path d="m16.3 16.3 2.8 2.8" /><path d="M3 12h4" /><path d="M17 12h4" /><path d="m4.9 19.1 2.8-2.8" /><path d="m16.3 7.7 2.8-2.8" /><circle cx="12" cy="12" r="3.5" /></svg>;
    case 'upload':
      return <svg {...p}><path d="M12 16V7" /><path d="m8 11 4-4 4 4" /><path d="M20 16.7A4 4 0 0 0 18 9h-1.3A6 6 0 1 0 5 10.3" /><path d="M8 16h8" /></svg>;
    case 'play':
      return <svg {...p} fill="currentColor" stroke="none"><path d="m8 5 11 7-11 7z" /></svg>;
    case 'refresh':
      return <svg {...p}><path d="M21 12a9 9 0 0 0-15.5-6.4" /><path d="M3 4v5h5" /><path d="M3 12a9 9 0 0 0 15.5 6.4" /><path d="M21 20v-5h-5" /></svg>;
    case 'download':
      return <svg {...p}><path d="M12 4v11" /><path d="m7 10 5 5 5-5" /><path d="M5 20h14" /></svg>;
    case 'dots':
      return <svg {...p}><circle cx="12" cy="5" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="12" cy="19" r="1.5" /></svg>;
    case 'chevronDown':
      return <svg {...p}><path d="m6 9 6 6 6-6" /></svg>;
    case 'chevronRight':
      return <svg {...p}><path d="m9 6 6 6-6 6" /></svg>;
    case 'check':
      return <svg {...p}><path d="m5 12 4 4 10-10" /></svg>;
    case 'circle':
      return <svg {...p}><circle cx="12" cy="12" r="8" /></svg>;
    case 'filter':
      return <svg {...p}><path d="M4 5h16l-6 7v5l-4 2v-7z" /></svg>;
    case 'copy':
      return <svg {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><rect x="4" y="4" width="11" height="11" rx="2" /></svg>;
    case 'list':
      return <svg {...p}><path d="M8 6h12" /><path d="M8 12h12" /><path d="M8 18h12" /><circle cx="4" cy="6" r="1" fill="currentColor" stroke="none" /><circle cx="4" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="4" cy="18" r="1" fill="currentColor" stroke="none" /></svg>;
    case 'grid':
      return <svg {...p}><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></svg>;
    case 'edit':
      return <svg {...p}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z" /></svg>;
    case 'link':
      return <svg {...p}><path d="M10 13a5 5 0 0 0 7.1 0l2.8-2.8a5 5 0 0 0-7.1-7.1L11.2 4" /><path d="M14 11a5 5 0 0 0-7.1 0L4 13.8a5 5 0 0 0 7.1 7.1L12.8 20" /></svg>;
    case 'clock':
      return <svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></svg>;
    case 'users':
      return <svg {...p}><path d="M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" /><circle cx="9.5" cy="7" r="3.5" /><path d="M21 21v-2a4 4 0 0 0-3-3.9" /><path d="M15 3.1a3.5 3.5 0 0 1 0 6.8" /></svg>;
    case 'badge':
      return <svg {...p}><circle cx="12" cy="8" r="5" /><path d="m8.5 13.5-1 7 4.5-2.5 4.5 2.5-1-7" /></svg>;
    case 'sliders':
      return <svg {...p}><path d="M4 21v-7" /><path d="M4 10V3" /><path d="M12 21v-9" /><path d="M12 8V3" /><path d="M20 21v-5" /><path d="M20 12V3" /><path d="M1 14h6" /><path d="M9 8h6" /><path d="M17 16h6" /></svg>;
    case 'menu':
      return <svg {...p}><path d="M4 7h16" /><path d="M4 12h16" /><path d="M4 17h16" /></svg>;
    default:
      return null;
  }
}

function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ');
}

function scoreOf(candidate: Candidate) {
  return Number(
    candidate._aggregated_best_score ??
      candidate.score ??
      candidate.ranking_score ??
      candidate.semantic_score ??
      candidate.historical_strength ??
      candidate.best_support_score ??
      candidate.avg_support_score ??
      candidate['@search.reranker_score'] ??
      candidate['@search.score'] ??
      0,
  ) || 0;
}

function percent(value: number) {
  return Math.round(Math.max(0, Math.min(1, value)) * 100);
}

function supportCountOf(candidate: Candidate) {
  return Number(candidate.support_count ?? candidate._support_count ?? 0) || 0;
}

function semanticScoreOf(candidate: Candidate) {
  return Number(candidate.semantic_score ?? candidate.score ?? candidate.ranking_score ?? 0) || 0;
}

function historicalScoreOf(candidate: Candidate) {
  return Number(candidate.historical_strength ?? candidate.best_support_score ?? candidate.avg_support_score ?? 0) || 0;
}

function bucketLabel(candidate: Candidate) {
  const bucket = String(candidate.bucket ?? candidate.candidate_source ?? '').trim();
  if (!bucket) return 'semantic only';
  if (bucket === 'semantic_plus_historical') return 'merged';
  return bucket.replace(/_/g, ' ');
}

function matchLabel(score: number) {
  if (score >= 0.88) return 'High Match';
  if (score >= 0.8) return 'Medium Match';
  return 'Broad Fit';
}

function confidenceTone(score: number) {
  if (score >= 0.88) return 'mint';
  if (score >= 0.8) return 'amber';
  return 'blue';
}

function sourceTone(source: string) {
  if (source.includes('merged')) return 'mint';
  if (source.includes('historical')) return 'violet';
  if (source.includes('semantic')) return 'blue';
  return 'neutral';
}

const TAB_ITEMS: Array<{ key: Tab; label: string }> = [
  { key: 'selection', label: 'Selection' },
  { key: 'comparison', label: 'Comparison' },
  { key: 'retrieval', label: 'Retrieval' },
  { key: 'vsCandidates', label: 'VS Candidates' },
  { key: 'historicCandidates', label: 'Historic Candidates' },
  { key: 'faissHits', label: 'FAISS Hits' },
  { key: 'mergedCandidates', label: 'Merged Candidates' },
  { key: 'reference', label: 'Reference' },
  { key: 'raw', label: 'Raw' },
];

const PIPELINE_STEPS: Array<{ key: Exclude<Step, 'idle' | 'done' | 'error'>; title: string; subtitle: string; icon: IconName }> = [
  { key: 'extract', title: 'Extract', subtitle: 'Read idea card', icon: 'file' },
  { key: 'condense', title: 'Condense', subtitle: 'LLM summarize', icon: 'bolt' },
  { key: 'semantic', title: 'VS Search', subtitle: 'Azure VS index', icon: 'search' },
  { key: 'historical', title: 'Historical', subtitle: 'FAISS lookup', icon: 'database' },
  { key: 'finalize', title: 'LLM Select', subtitle: 'GPT picks VS', icon: 'spark' },
];

const SAMPLE_SELECTED: SelectedVS[] = [
  { entity_id: 'VS-04231', entity_name: 'Automated Claim Intake', confidence: 0.92, reason: 'Strong alignment to intake capture, validation, and routing workflows.', category: 'Claim Management' },
  { entity_id: 'VS-01877', entity_name: 'Provider Data Management', confidence: 0.88, reason: 'Provider data synchronization and enrichment appear central to the idea card.', category: 'Provider Operations' },
  { entity_id: 'VS-03102', entity_name: 'Prior Auth Orchestration', confidence: 0.86, reason: 'Workflow orchestration, decisioning, and utilization signals are present.', category: 'Utilization Management' },
  { entity_id: 'VS-00988', entity_name: 'Member Onboarding Experience', confidence: 0.8, reason: 'Member-facing setup and activation flows show moderate support.', category: 'Member Services' },
];

const SAMPLE_REJECTED: RejectedVS[] = [
  { entity_id: 'VS-02765', entity_name: 'Payment Integrity & Analytics', reason: 'Adjacent theme, but lower evidence than selected candidates.' },
];

const SAMPLE_CANONICAL: CanonicalVS[] = [
  { id: 'VS-04231', name: 'Automated Claim Intake', category: 'Claim Management' },
  { id: 'VS-01877', name: 'Provider Data Management', category: 'Provider Operations' },
  { id: 'VS-03102', name: 'Prior Authorization Orchestration', category: 'Utilization Management' },
  { id: 'VS-00988', name: 'Member Onboarding Experience', category: 'Member Services' },
  { id: 'VS-00511', name: 'Eligibility & Benefits Verification', category: 'Eligibility' },
];

const SAMPLE_SEMANTIC: Candidate[] = [
  { entity_id: 'VS-04231', entity_name: 'Automated Claim Intake', score: 0.92, description: 'Streamlines inbound claim capture, validation, and routing for faster intake.', historical_reasons: ['Claims', 'Intake', 'Automation'] },
  { entity_id: 'VS-01877', entity_name: 'Provider Data Management', score: 0.88, description: 'Maintains and synchronizes provider data to ensure accuracy and availability.', historical_reasons: ['Provider', 'Data', 'Master Data'] },
  { entity_id: 'VS-03102', entity_name: 'Prior Auth Orchestration', score: 0.85, description: 'Orchestrates prior authorization workflow and decisioning.', historical_reasons: ['Prior Auth', 'Workflow', 'Automation'] },
  { entity_id: 'VS-00988', entity_name: 'Member Onboarding Experience', score: 0.79, description: 'Simplifies new member enrollment and welcome experience.', historical_reasons: ['Onboarding', 'Member', 'Experience'] },
  { entity_id: 'VS-02765', entity_name: 'Payment Integrity & Analytics', score: 0.74, description: 'Detects payment anomalies and delivers actionable insights.', historical_reasons: ['Analytics', 'Payments', 'Integrity'] },
];

const SAMPLE_HISTORIC: Candidate[] = [
  { entity_id: 'VS-04231', entity_name: 'Automated Claim Intake', support_count: 128, direct_count: 72, implied_count: 56, best_support_score: 0.92, historical_reasons: ['Similar automation of intake, data capture, and validation workflows.'] },
  { entity_id: 'VS-01877', entity_name: 'Provider Data Management', support_count: 96, direct_count: 48, implied_count: 48, best_support_score: 0.88, historical_reasons: ['Provider data normalization, matching, and enrichment.'] },
  { entity_id: 'VS-03102', entity_name: 'Prior Auth Orchestration', support_count: 84, direct_count: 42, implied_count: 42, best_support_score: 0.86, historical_reasons: ['Workflow orchestration and prior authorization processes.'] },
  { entity_id: 'VS-00988', entity_name: 'Member Onboarding Experience', support_count: 74, direct_count: 28, implied_count: 46, best_support_score: 0.8, historical_reasons: ['Member onboarding flows and digital experience.'] },
  { entity_id: 'VS-02611', entity_name: 'Claims Status & Tracking', support_count: 61, direct_count: 26, implied_count: 35, best_support_score: 0.77, historical_reasons: ['Claims status visibility and tracking enhancements.'] },
];

const SAMPLE_FAISS: HistoricalTicketHit[] = [
  { ticket_id: 'IDMT-16420', best_score: 0.94, title: 'Automated Claim Intake', summary_preview: 'Implemented automated ingestion and validation of electronic claims to reduce manual effort and improve accuracy.', direct_vs_names: ['Claim Management'], implied_vs_names: ['Intake Automation'] },
  { ticket_id: 'IDMT-15211', best_score: 0.89, title: 'Provider Data Management Enhancements', summary_preview: 'Improved provider data ingestion, deduplication, and monitoring with dashboard workflows.', direct_vs_names: ['Data Management'], implied_vs_names: ['Provider Ops'] },
  { ticket_id: 'IDMT-13987', best_score: 0.86, title: 'Prior Authorization Orchestration', summary_preview: 'Orchestrated prior auth workflows across payer systems with decisioning and notifications.', direct_vs_names: ['Prior Authorization'], implied_vs_names: ['Utilization Mgmt'] },
  { ticket_id: 'IDMT-12763', best_score: 0.82, title: 'Member Onboarding Experience Optimization', summary_preview: 'Redesigned member onboarding flows with guided forms and document capture.', direct_vs_names: ['Member Experience'], implied_vs_names: ['Onboarding'] },
  { ticket_id: 'IDMT-11358', best_score: 0.78, title: 'Provider Portal Data Quality Dashboard', summary_preview: 'Introduced dashboard for provider data quality KPIs with remediation tasks.', direct_vs_names: ['Data Management'], implied_vs_names: ['Provider Ops'] },
];

const SAMPLE_MERGED: Candidate[] = [
  { entity_id: 'VS-04231', entity_name: 'Automated Claim Intake', score: 0.92, bucket: 'semantic_plus_historical', from_semantic: true, from_historical: true, semantic_score: 0.9, historical_strength: 0.88, support_count: 12, historical_reasons: ['Strong semantic match on intake automation; confirmed by historical hits in claims intake initiatives.'] },
  { entity_id: 'VS-01877', entity_name: 'Provider Data Management', score: 0.88, bucket: 'semantic_plus_historical', from_semantic: true, from_historical: true, semantic_score: 0.84, historical_strength: 0.76, support_count: 11, historical_reasons: ['High alignment on provider data operations and governance; supported by historical provider master data programs.'] },
  { entity_id: 'VS-03102', entity_name: 'Prior Auth Orchestration', score: 0.86, bucket: 'semantic_plus_historical', from_semantic: true, from_historical: true, semantic_score: 0.79, historical_strength: 0.71, support_count: 10, historical_reasons: ['Strong semantic relevance to prior authorization workflows; backed by multiple historical orchestration efforts.'] },
  { entity_id: 'VS-00988', entity_name: 'Member Onboarding Experience', score: 0.8, bucket: 'semantic_only', from_semantic: true, from_historical: false, semantic_score: 0.75, historical_strength: 0.2, support_count: 7, historical_reasons: ['Strong semantic alignment to onboarding and activation experiences; limited historical activity found.'] },
  { entity_id: 'VS-01425', entity_name: 'Document Intelligence & OCR', score: 0.72, bucket: 'semantic_plus_historical', from_semantic: true, from_historical: true, semantic_score: 0.69, historical_strength: 0.66, support_count: 8, historical_reasons: ['Good alignment on document understanding and OCR capabilities; historical support from document automation projects.'] },
  { entity_id: 'VS-02216', entity_name: 'Payment Accuracy & Reconciliation', score: 0.68, bucket: 'historical_only', from_semantic: false, from_historical: true, semantic_score: 0.08, historical_strength: 0.63, support_count: 6, historical_reasons: ['Strong historical precedent in payment accuracy and reconciliation; limited semantic match to the idea.'] },
];

const SAMPLE_RAW = {
  success: true,
  run_id: 'd7eb6bd2-2f5a-4a2a-9c1b-0a4f9e9b3c2d',
  pipeline_version: 'v2.4',
  source_document: {
    id: 'IDMT-19761',
    title: 'Intelligent Delivery Platform',
    source: 'Idea Management',
    created: '2024-05-08T10:42:31Z',
    owner: 'Manisha Reddy Chargall',
  },
  candidates: [
    {
      candidate_id: 'VS-04231',
      value_stream_name: 'Automated Claim Intake',
      stage: 'Claim Management',
      confidence: 0.92,
      rationale: 'Matches automated intake, document classification, and initial validation workflows.',
    },
  ],
};

function statusLabel(step: Step) {
  if (step === 'done') return 'Ready';
  if (step === 'error') return 'Error';
  if (step === 'idle') return 'Idle';
  return 'Running';
}

function stageStatus(step: Step, key: Exclude<Step, 'idle' | 'done' | 'error'>, stepOnError: Step) {
  const order = PIPELINE_STEPS.map((item) => item.key);
  const active = step === 'error' ? stepOnError : step;
  const activeIndex = order.indexOf(active as Exclude<Step, 'idle' | 'done' | 'error'>);
  const currentIndex = order.indexOf(key);

  if (step === 'done') return 'done';
  if (step === 'error') {
    if (currentIndex < activeIndex) return 'done';
    if (currentIndex === activeIndex) return 'error';
    return 'idle';
  }
  if (activeIndex === -1) return 'idle';
  if (currentIndex < activeIndex) return 'done';
  if (currentIndex === activeIndex) return 'active';
  return 'idle';
}

function Panel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <section className={cn('vse-panel', className)}>{children}</section>;
}

function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'mint' | 'blue' | 'amber' | 'violet' }) {
  return <span className={cn('vse-badge', `vse-badge-${tone}`)}>{children}</span>;
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 rounded-full bg-[var(--vse-track)]">
      <div className="h-1.5 rounded-full bg-[var(--vse-accent)]" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

function StageCard({
  title,
  subtitle,
  icon,
  status,
  connector,
}: {
  title: string;
  subtitle: string;
  icon: IconName;
  status: 'idle' | 'active' | 'done' | 'error';
  connector?: boolean;
}) {
  return (
    <div className="flex min-w-[196px] items-center gap-4">
      <div
        className={cn(
          'flex-1 rounded-[18px] border px-5 py-4 shadow-[0_12px_32px_rgba(15,23,42,0.04)]',
          status === 'active' && 'border-[var(--vse-accent-soft)] bg-[linear-gradient(180deg,#ffffff_0%,#f2fbfa_100%)]',
          status === 'done' && 'border-[var(--vse-border)] bg-white',
          status === 'idle' && 'border-[var(--vse-border)] bg-white',
          status === 'error' && 'border-[#efb1af] bg-[#fff6f5]',
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex gap-3">
            <div className="mt-0.5 text-[var(--vse-ink)]"><Icon name={icon} className="h-6 w-6" /></div>
            <div>
              <div className="text-[1rem] font-semibold text-[var(--vse-ink)]">{title}</div>
              <div className="mt-1 text-sm text-[var(--vse-muted)]">{subtitle}</div>
            </div>
          </div>
          <div
            className={cn(
              'mt-8 flex h-5 w-5 items-center justify-center rounded-full border',
              status === 'error' ? 'border-[#efb1af] text-[#d0473f]' : 'border-[var(--vse-accent-soft)] text-[var(--vse-accent)]',
            )}
          >
            {status === 'done' || status === 'active' ? <Icon name="check" className="h-3 w-3" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
          </div>
        </div>
      </div>
      {connector && <div className="hidden h-[2px] w-10 bg-[var(--vse-accent)]/80 xl:block" />}
    </div>
  );
}

function TabNav({ tab, onChange }: { tab: Tab; onChange: (next: Tab) => void }) {
  return (
    <div className="flex flex-wrap gap-x-8 gap-y-3 border-b border-[var(--vse-border)] px-3 pt-2">
      {TAB_ITEMS.map((item) => (
        <button
          key={item.key}
          onClick={() => onChange(item.key)}
          className={cn(
            'relative pb-4 text-[0.99rem] transition-colors',
            tab === item.key ? 'font-semibold text-[var(--vse-accent)]' : 'text-[var(--vse-muted)] hover:text-[var(--vse-ink)]',
          )}
        >
          {item.label}
          {tab === item.key && <span className="absolute inset-x-0 bottom-0 h-[3px] rounded-full bg-[var(--vse-accent)]" />}
        </button>
      ))}
    </div>
  );
}

function SelectionView({ selected, rejected }: { selected: SelectedVS[]; rejected: RejectedVS[] }) {
  return (
    <div className="space-y-5">
      <div className="px-4 pt-4">
        <div className="flex items-center gap-2">
          <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Primary Value Streams</h2>
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
        </div>
      </div>

      <div className="space-y-4 px-4 pb-4">
        {selected.map((item, index) => {
          const chips = [
            item.category ?? 'Claim Management',
            index === 0 ? 'Intake Automation' : index === 1 ? 'Provider Ops' : index === 2 ? 'Workflow' : 'Onboarding',
            index === 0 ? 'Workflow' : index === 1 ? 'Data Quality' : index === 2 ? 'Utilization Mgmt' : 'Member Experience',
          ];

          return (
            <div key={`${item.entity_id}-${index}`} className="rounded-[20px] border border-[var(--vse-border)] bg-white px-5 py-5 shadow-[0_12px_24px_rgba(15,23,42,0.03)]">
              <div className="flex flex-col gap-4 xl:flex-row xl:items-center">
                <div className="flex min-w-0 items-center gap-5">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-[var(--vse-border)] text-xl text-[var(--vse-muted)]">{index + 1}</div>
                  <div className="min-w-0">
                    <div className="text-[1.55rem] font-semibold tracking-[-0.025em] text-[var(--vse-ink)]">
                      {item.entity_name}
                      <span className="ml-2 font-normal text-[var(--vse-muted)]">({item.entity_id})</span>
                    </div>
                    <p className="mt-2 max-w-[820px] text-[0.98rem] leading-7 text-[var(--vse-muted)]">{item.reason}</p>
                  </div>
                </div>

                <div className="flex flex-1 flex-wrap items-center gap-3 xl:justify-end">
                  {chips.map((chip) => (
                    <Badge key={chip}>{chip}</Badge>
                  ))}
                  <Badge tone="mint">{percent(item.confidence)}% Confidence</Badge>
                  <button className="inline-flex h-10 w-10 items-center justify-center rounded-full text-[var(--vse-muted)] transition hover:bg-[var(--vse-soft)] hover:text-[var(--vse-ink)]">
                    <Icon name="chevronRight" className="h-5 w-5" />
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        {rejected.length > 0 && (
          <Panel className="px-5 py-4">
            <div className="mb-3 text-sm font-semibold text-[var(--vse-ink)]">Not Selected</div>
            <div className="space-y-3">
              {rejected.map((item) => (
                <div key={item.entity_id} className="rounded-2xl border border-[var(--vse-border)] bg-[var(--vse-soft)] px-4 py-3">
                  <div className="font-medium text-[var(--vse-ink)]">{item.entity_name}</div>
                  <div className="mt-1 text-sm text-[var(--vse-muted)]">{item.reason}</div>
                </div>
              ))}
            </div>
          </Panel>
        )}

        <button className="flex w-full items-center justify-between rounded-[18px] border border-[var(--vse-border)] bg-white px-6 py-5 text-left text-[1rem] text-[var(--vse-muted)] shadow-[0_8px_20px_rgba(15,23,42,0.03)]">
          <span>View more value stream candidates (16)</span>
          <Icon name="chevronDown" className="h-5 w-5" />
        </button>
      </div>
    </div>
  );
}

function ComparisonView({ candidates }: { candidates: Candidate[] }) {
  const columns = candidates.slice(0, 4);

  return (
    <div className="space-y-4 px-4 py-4">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Compare Candidate Value Streams</h2>
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
          </div>
          <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">Review how candidate value streams compare across key quality and relevance dimensions.</p>
        </div>
        <button className="vse-button-secondary">
          <Icon name="download" />
          Download Comparison
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button className="vse-icon-button"><Icon name="filter" /></button>
        <button className="vse-filter-chip">Min Confidence: 70%+ ×</button>
        <button className="vse-filter-chip">Domain: All <Icon name="chevronDown" className="h-4 w-4" /></button>
        <button className="vse-filter-chip">Source: All <Icon name="chevronDown" className="h-4 w-4" /></button>
        <button className="text-[0.96rem] font-medium text-[var(--vse-accent)]">Reset</button>
      </div>

      <Panel className="overflow-hidden">
        <div className="grid min-w-[1020px] grid-cols-[180px_repeat(4,minmax(0,1fr))]">
          <div className="border-r border-[var(--vse-border)] p-6">
            <div className="flex items-center gap-2 text-[1rem] font-semibold text-[var(--vse-ink)]">
              Metric
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
            </div>
          </div>
          {columns.map((candidate, index) => (
            <div key={candidate.entity_id} className="border-r border-[var(--vse-border)] p-6 last:border-r-0">
              <div className="flex items-start gap-4">
                <div className="flex h-11 w-11 items-center justify-center rounded-full border border-[var(--vse-border)] text-lg text-[var(--vse-muted)]">{index + 1}</div>
                <div>
                  <div className="text-[1.12rem] font-semibold leading-7 text-[var(--vse-ink)]">{candidate.entity_name}</div>
                  <div className="text-[1.02rem] text-[var(--vse-muted)]">({candidate.entity_id})</div>
                </div>
              </div>
            </div>
          ))}

          {[
            { label: 'Semantic Score (vs. idea)', values: columns.map((candidate) => semanticScoreOf(candidate) || scoreOf(candidate)) },
            { label: 'Historical Support', values: columns.map((candidate) => historicalScoreOf(candidate) || Math.max(0.68, scoreOf(candidate) - 0.08)) },
            { label: 'Domain Relevance', values: columns.map((candidate) => Math.max(0.6, (semanticScoreOf(candidate) || scoreOf(candidate)) - 0.02)) },
          ].map((row) => (
            <>
              <div key={`${row.label}-label`} className="border-r border-t border-[var(--vse-border)] px-4 py-5 text-[0.95rem] text-[var(--vse-ink)]">{row.label}</div>
              {row.values.map((value, index) => (
                <div key={`${row.label}-${index}`} className="border-r border-t border-[var(--vse-border)] px-4 py-5 last:border-r-0">
                  <div className="flex items-center gap-4">
                    <div className="flex-1"><ProgressBar value={percent(value)} /></div>
                    <div className="w-10 text-right text-[0.98rem] text-[var(--vse-ink)]">{value.toFixed(2)}</div>
                  </div>
                </div>
              ))}
            </>
          ))}

          <div className="border-r border-t border-[var(--vse-border)] px-4 py-5 text-[0.95rem] text-[var(--vse-ink)]">Confidence</div>
          {columns.map((candidate) => (
            <div key={`${candidate.entity_id}-confidence`} className="border-r border-t border-[var(--vse-border)] px-4 py-5 last:border-r-0">
              <Badge tone="mint">{percent(scoreOf(candidate))}% High</Badge>
            </div>
          ))}

          <div className="border-r border-t border-[var(--vse-border)] px-4 py-5 text-[0.95rem] text-[var(--vse-ink)]">Source Coverage</div>
          {columns.map((candidate, index) => (
            <div key={`${candidate.entity_id}-coverage`} className="border-r border-t border-[var(--vse-border)] px-4 py-5 text-[0.96rem] text-[var(--vse-ink)] last:border-r-0">
              {14 - index} / 17 sources
            </div>
          ))}

          <div className="border-r border-t border-[var(--vse-border)] px-4 py-5 text-[0.95rem] text-[var(--vse-ink)]">Rationale (Pros / Cons)</div>
          {columns.map((candidate, index) => (
            <div key={`${candidate.entity_id}-reasons`} className="border-r border-t border-[var(--vse-border)] px-4 py-5 last:border-r-0">
              <div className="space-y-3 text-[0.95rem] leading-6">
                <div className="flex items-start gap-2 text-[var(--vse-ink)]">
                  <span className="mt-1 text-[var(--vse-accent)]"><Icon name="check" className="h-4 w-4" /></span>
                  <span>{index === 0 ? 'Strong semantic match to intake and automation.' : index === 1 ? 'Strong domain fit; broad applicability.' : index === 2 ? 'Impactful leverage on cycle time.' : 'High member impact; clear outcomes.'}</span>
                </div>
                <div className="flex items-start gap-2 text-[var(--vse-muted)]">
                  <span className="mt-1 text-[#f05c4d]"><Icon name="circle" className="h-4 w-4" /></span>
                  <span>{index === 0 ? 'Narrower scope; less upstream context.' : index === 1 ? 'Slightly lower semantic alignment.' : index === 2 ? 'Weaker historical evidence.' : 'Lower historical coverage.'}</span>
                </div>
                <button className="text-[0.96rem] font-medium text-[var(--vse-accent)]">1 more</button>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <button className="flex w-full items-center justify-between rounded-[18px] border border-[var(--vse-border)] bg-white px-6 py-5 text-left text-[1rem] text-[var(--vse-muted)] shadow-[0_8px_20px_rgba(15,23,42,0.03)]">
        <span>View additional candidates (12)</span>
        <Icon name="chevronDown" className="h-5 w-5" />
      </button>
    </div>
  );
}

function RetrievalView() {
  const cards = [
    { title: 'Semantic Search', subtitle: 'Azure AI Search over VS index', tags: ['member onboarding', 'automation', 'identity management', '+3'], left: 'Results', leftValue: '1,248', right: 'Unique VS', rightValue: '612', icon: 'search' as IconName },
    { title: 'Historical Search', subtitle: 'FAISS similarity lookup', tags: ['claim intake', 'provider data mgmt', 'prior authorization', '+2'], left: 'Results', leftValue: '842', right: 'Unique VS', rightValue: '497', icon: 'database' as IconName },
    { title: 'Candidate Pool', subtitle: 'Combined pre-merge pool', tags: ['onboarding experience', 'data management', 'workflow automation', '+3'], left: 'Total Candidates', leftValue: '1,894', right: 'Unique VS', rightValue: '862', icon: 'users' as IconName },
    { title: 'Merged Results', subtitle: 'After de-duplication & ranking', tags: ['automation', 'member onboarding', 'data management', '+3'], left: 'Final Candidates', leftValue: '250', right: 'Unique VS', rightValue: '212', icon: 'link' as IconName },
  ];

  const flow = [
    { title: 'Parse & Condense', detail: 'Idea card parsed and summarized to form the retrieval query.', time: '00.02s', icon: 'file' as IconName },
    { title: 'Semantic Search', detail: 'Queried Azure VS index with condensed query.', time: '01.34s', icon: 'search' as IconName },
    { title: 'Historical Search', detail: 'Ran FAISS lookup on historical value-stream embeddings.', time: '00.87s', icon: 'database' as IconName },
    { title: 'Candidate Pool', detail: 'Combined and prepared candidates for deduplication.', time: '00.21s', icon: 'users' as IconName },
    { title: 'Merge & Rank', detail: 'De-duplicated, re-ranked and selected top results.', time: '00.46s', icon: 'badge' as IconName },
  ];

  return (
    <div className="space-y-5 px-4 py-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Retrieval Overview</h2>
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
        </div>
        <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">A summary of what was searched, where, and what was found.</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-4">
        {cards.map((card) => (
          <Panel key={card.title} className="p-5">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--vse-soft)] text-[var(--vse-accent)]">
                <Icon name={card.icon} className="h-6 w-6" />
              </div>
              <div className="min-w-0">
                <div className="text-[1.2rem] font-semibold text-[var(--vse-ink)]">{card.title}</div>
                <div className="text-[0.95rem] text-[var(--vse-muted)]">{card.subtitle}</div>
              </div>
            </div>
            <div className="mt-5">
              <div className="mb-2 text-sm font-semibold text-[var(--vse-ink)]">Top Signals</div>
              <div className="flex flex-wrap gap-2">
                {card.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}
              </div>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-4">
              <div>
                <div className="text-sm text-[var(--vse-muted)]">{card.left}</div>
                <div className="mt-1 text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{card.leftValue}</div>
              </div>
              <div>
                <div className="text-sm text-[var(--vse-muted)]">{card.right}</div>
                <div className="mt-1 text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{card.rightValue}</div>
              </div>
            </div>
          </Panel>
        ))}
      </div>

      <Panel className="p-5">
        <div className="mb-6 text-[1.2rem] font-semibold text-[var(--vse-ink)]">Retrieval Flow</div>
        <div className="grid gap-5 xl:grid-cols-5">
          {flow.map((item, index) => (
            <div key={item.title} className="relative">
              {index < flow.length - 1 && <div className="absolute left-[60%] top-6 hidden h-[2px] w-[80%] border-t border-dashed border-[var(--vse-border-strong)] xl:block" />}
              <div className="flex flex-col items-center text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-full border border-[var(--vse-accent-soft)] bg-[var(--vse-soft)] text-[var(--vse-accent)]">
                  <Icon name={item.icon} className="h-6 w-6" />
                </div>
                <div className="mt-5 flex h-8 w-8 items-center justify-center rounded-full border border-[var(--vse-border)] text-sm text-[var(--vse-muted)]">{index + 1}</div>
                <div className="mt-4 text-[1.05rem] font-semibold text-[var(--vse-ink)]">{item.title}</div>
                <div className="mt-2 text-[0.95rem] leading-6 text-[var(--vse-muted)]">{item.detail}</div>
                <div className="mt-4 text-[0.95rem] text-[var(--vse-ink)]">{item.time}</div>
                <div className="mt-2 text-[var(--vse-accent)]"><Icon name="check" className="h-4 w-4" /></div>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.9fr]">
        <Panel className="p-5">
          <div className="mb-5 flex items-center gap-2 text-[1.2rem] font-semibold text-[var(--vse-ink)]">
            Top Matched Signals
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
          </div>
          <div className="space-y-5">
            {[
              { signal: 'member onboarding', split: '68% / 32%', matches: 246, value: 68 },
              { signal: 'claim intake automation', split: '54% / 46%', matches: 193, value: 54 },
              { signal: 'provider data management', split: '47% / 53%', matches: 168, value: 47 },
            ].map((row, index) => (
              <div key={row.signal} className="grid grid-cols-[40px_1.2fr_1fr_80px] items-center gap-4">
                <div className="flex h-8 w-8 items-center justify-center rounded-full border border-[var(--vse-border)] text-sm text-[var(--vse-muted)]">{index + 1}</div>
                <div className="text-[1rem] font-medium text-[var(--vse-ink)]">{row.signal}</div>
                <div className="space-y-1">
                  <ProgressBar value={row.value} />
                  <div className="text-sm text-[var(--vse-muted)]">{row.split}</div>
                </div>
                <div className="text-right text-[1rem] text-[var(--vse-ink)]">{row.matches}</div>
              </div>
            ))}
          </div>
          <div className="mt-6 flex gap-6 text-sm text-[var(--vse-muted)]">
            <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[var(--vse-accent)]" />Semantic</div>
            <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-[#c7d1ea]" />Historical</div>
          </div>
        </Panel>

        <Panel className="flex items-center gap-5 p-6">
          <div className="flex h-18 w-18 items-center justify-center rounded-full border border-[var(--vse-border)] bg-[var(--vse-soft)] text-[var(--vse-accent)]">
            <Icon name="badge" className="h-8 w-8" />
          </div>
          <div>
            <p className="max-w-[460px] text-[1.12rem] leading-8 text-[var(--vse-ink)]">
              We searched across semantic and historical sources and retrieved <strong>250</strong> high-quality candidates covering <strong>212</strong> unique value streams.
            </p>
            <div className="mt-5 text-[0.98rem] text-[var(--vse-muted)]">Sources used: Azure VS Index, FAISS (Historical)</div>
            <div className="mt-2 text-[0.98rem] text-[var(--vse-muted)]">Search completed in 02.90s</div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function VsCandidatesView({ candidates }: { candidates: Candidate[] }) {
  return (
    <div className="space-y-4 px-4 py-4">
      <div>
        <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Semantic Value Stream Candidates</h2>
        <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">Top matches from the value stream index based on semantic relevance to the idea.</p>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.3fr_0.55fr_0.55fr_0.55fr_auto]">
        <div className="vse-field"><Icon name="search" className="h-4 w-4 text-[var(--vse-muted)]" /><input className="vse-input-reset" placeholder="Search candidates by name or ID..." /></div>
        <button className="vse-select-button">Min Semantic Score 70 <Icon name="chevronDown" className="h-4 w-4" /></button>
        <button className="vse-select-button">Category All <Icon name="chevronDown" className="h-4 w-4" /></button>
        <button className="vse-select-button">Match Type All <Icon name="chevronDown" className="h-4 w-4" /></button>
        <button className="vse-button-secondary"><Icon name="refresh" />Clear Filters</button>
      </div>

      <div className="space-y-3">
        {candidates.slice(0, 5).map((candidate, index) => {
          const score = scoreOf(candidate);
          const expanded = index === 1;
          const tags = Array.isArray(candidate.historical_reasons) ? candidate.historical_reasons.slice(0, 3) : [];

          return (
            <Panel key={candidate.entity_id} className="overflow-hidden">
              <div className="flex flex-col gap-4 px-5 py-4 xl:flex-row xl:items-center">
                <div className="flex min-w-0 items-start gap-5 xl:flex-[1.2]">
                  <div className="flex h-11 w-11 items-center justify-center rounded-full border border-[var(--vse-border)] text-lg text-[var(--vse-muted)]">{index + 1}</div>
                  <div className="min-w-0">
                    <div className="text-[1.28rem] font-semibold text-[var(--vse-ink)]">
                      {candidate.entity_name}
                      <span className="ml-2 font-normal text-[var(--vse-muted)]">({candidate.entity_id})</span>
                    </div>
                    <p className="mt-1 text-[0.98rem] leading-7 text-[var(--vse-muted)]">{candidate.description ?? 'Candidate description unavailable.'}</p>
                  </div>
                </div>

                <div className="xl:w-[160px]">
                  <div className="text-sm text-[var(--vse-muted)]">Semantic Score</div>
                  <div className="mt-1 text-[1.8rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{percent(score)}%</div>
                </div>

                <div className="flex flex-1 flex-wrap gap-2">
                  {tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}
                </div>

                <div className="flex items-center gap-3">
                  <Badge tone={confidenceTone(score)}>{matchLabel(score)}</Badge>
                  <button className="inline-flex h-10 w-10 items-center justify-center rounded-full text-[var(--vse-muted)] transition hover:bg-[var(--vse-soft)] hover:text-[var(--vse-ink)]">
                    <Icon name={expanded ? 'chevronDown' : 'chevronRight'} className="h-5 w-5" />
                  </button>
                </div>
              </div>

              {expanded && (
                <div className="grid gap-6 border-t border-[var(--vse-border)] bg-[linear-gradient(180deg,rgba(247,250,252,0.95)_0%,rgba(255,255,255,1)_100%)] px-6 py-5 xl:grid-cols-5">
                  <div>
                    <div className="text-sm font-semibold text-[var(--vse-ink)]">Why it matches</div>
                    <p className="mt-2 text-[0.96rem] leading-7 text-[var(--vse-muted)]">
                      Strong alignment on data management, provider onboarding, and data quality automation.
                    </p>
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-[var(--vse-ink)]">Key Capabilities</div>
                    <ul className="mt-2 space-y-1 text-[0.96rem] leading-7 text-[var(--vse-muted)]">
                      <li>Provider data validation & enrichment</li>
                      <li>Data synchronization</li>
                      <li>Data quality monitoring</li>
                    </ul>
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-[var(--vse-ink)]">Primary Systems</div>
                    <p className="mt-2 text-[0.96rem] leading-7 text-[var(--vse-muted)]">Provider Hub, MDM, EDI Gateway</p>
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-[var(--vse-ink)]">Reference</div>
                    <p className="mt-2 text-[0.96rem] leading-7 text-[var(--vse-muted)]">FASS-24516</p>
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-[var(--vse-ink)]">Last Updated</div>
                    <p className="mt-2 text-[0.96rem] leading-7 text-[var(--vse-muted)]">Apr 12, 2024</p>
                  </div>
                </div>
              )}
            </Panel>
          );
        })}

        <button className="flex w-full items-center justify-between rounded-[18px] border border-[var(--vse-border)] bg-white px-6 py-5 text-left text-[1rem] text-[var(--vse-muted)] shadow-[0_8px_20px_rgba(15,23,42,0.03)]">
          <span>View more candidates (15)</span>
          <Icon name="chevronDown" className="h-5 w-5" />
        </button>
      </div>
    </div>
  );
}

function HistoricCandidatesView({ candidates }: { candidates: Candidate[] }) {
  const totals = candidates.reduce(
    (acc, candidate) => {
      acc.support += Number(candidate.support_count ?? 0);
      acc.direct += Number(candidate.direct_count ?? 0);
      acc.implied += Number(candidate.implied_count ?? 0);
      return acc;
    },
    { support: 0, direct: 0, implied: 0 },
  );

  return (
    <div className="space-y-4 px-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Historical Candidate Value Streams</h2>
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
          </div>
          <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">Ranked by historical support from past tickets and change activity.</p>
        </div>
        <button className="vse-button-secondary">
          <Icon name="filter" />
          Filters
        </button>
      </div>

      <Panel className="overflow-hidden">
        <div className="grid min-w-[1100px] grid-cols-[70px_2.2fr_130px_170px_140px_140px_170px_1.5fr] items-center border-b border-[var(--vse-border)] px-3 py-4 text-sm text-[var(--vse-muted)]">
          <div>Rank</div>
          <div>Value Stream</div>
          <div>Support Count</div>
          <div>Best Support Score</div>
          <div>Direct Tickets</div>
          <div>Implied Tickets</div>
          <div>Last Supporting Ticket</div>
          <div>Reason Summary</div>
        </div>

        {candidates.slice(0, 5).map((candidate, index) => (
          <div key={candidate.entity_id} className="grid min-w-[1100px] grid-cols-[70px_2.2fr_130px_170px_140px_140px_170px_1.5fr] items-center border-b border-[var(--vse-border)] px-3 py-5 last:border-b-0">
            <div>
              <div className="flex h-10 w-10 items-center justify-center rounded-full border border-[var(--vse-border)] text-lg text-[var(--vse-muted)]">{index + 1}</div>
            </div>
            <div>
              <div className="text-[1.18rem] font-semibold text-[var(--vse-ink)]">
                {candidate.entity_name}
                <span className="ml-2 font-normal text-[var(--vse-muted)]">({candidate.entity_id})</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge tone="blue">direct support</Badge>
                <Badge tone="mint">implied support</Badge>
              </div>
            </div>
            <div className="text-[1.1rem] text-[var(--vse-ink)]">{candidate.support_count ?? 0}</div>
            <div><Badge tone="mint">{(candidate.best_support_score ?? 0).toFixed(2)}</Badge></div>
            <div className="text-[1.1rem] text-[var(--vse-ink)]">{candidate.direct_count ?? 0}</div>
            <div className="text-[1.1rem] text-[var(--vse-ink)]">{candidate.implied_count ?? 0}</div>
            <div className="text-[1rem] text-[var(--vse-ink)]">{index === 0 ? 'Apr 28, 2024' : index === 1 ? 'Apr 15, 2024' : index === 2 ? 'Apr 10, 2024' : index === 3 ? 'Mar 28, 2024' : 'Mar 18, 2024'}</div>
            <div className="flex items-center justify-between gap-3 text-[0.98rem] leading-7 text-[var(--vse-muted)]">
              <span>{candidate.historical_reasons?.[0] ?? 'Historical signal summary unavailable.'}</span>
              <Icon name="chevronRight" className="h-5 w-5 shrink-0 text-[var(--vse-muted)]" />
            </div>
          </div>
        ))}
      </Panel>

      <Panel className="grid gap-4 px-6 py-5 xl:grid-cols-6">
        <div>
          <div className="text-sm text-[var(--vse-muted)]">Total Candidates</div>
          <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">16</div>
        </div>
        <div>
          <div className="text-sm text-[var(--vse-muted)]">Total Support Count</div>
          <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{totals.support}</div>
          <div className="mt-1 text-sm text-[var(--vse-muted)]">Across all candidates</div>
        </div>
        <div>
          <div className="text-sm text-[var(--vse-muted)]">Total Direct Tickets</div>
          <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{totals.direct}</div>
          <div className="mt-1 text-sm text-[var(--vse-muted)]">33% of all support</div>
        </div>
        <div>
          <div className="text-sm text-[var(--vse-muted)]">Total Implied Tickets</div>
          <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{totals.implied}</div>
          <div className="mt-1 text-sm text-[var(--vse-muted)]">67% of all support</div>
        </div>
        <div>
          <div className="text-sm text-[var(--vse-muted)]">Unique Supporting Tickets</div>
          <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">487</div>
          <div className="mt-1 text-sm text-[var(--vse-muted)]">Across all candidates</div>
        </div>
        <div>
          <div className="text-sm text-[var(--vse-muted)]">Supporting Coverage</div>
          <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">81%</div>
          <div className="mt-1 text-sm text-[var(--vse-muted)]">Of historical ticket universe</div>
        </div>
      </Panel>
    </div>
  );
}

function FaissHitsView({ hits }: { hits: HistoricalTicketHit[] }) {
  return (
    <div className="space-y-4 px-4 py-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Historical Ticket Matches</h2>
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
        </div>
        <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">Top matches from the historical index based on semantic similarity.</p>
      </div>

      <Panel className="px-6 py-5">
        <div className="grid gap-6 xl:grid-cols-[220px_220px_1fr]">
          <div className="flex items-center gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[var(--vse-soft)] text-[var(--vse-accent)]">
              <Icon name="search" className="h-6 w-6" />
            </div>
            <div>
              <div className="text-sm text-[var(--vse-muted)]">Total Hits</div>
              <div className="mt-1 text-[2.2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">256</div>
            </div>
          </div>
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Average Score</div>
            <div className="mt-1 text-[2.2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">0.84</div>
          </div>
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Top Matched Themes</div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge>Provider Data Management</Badge>
              <Badge>Prior Auth Automation</Badge>
              <Badge>Onboarding</Badge>
              <Badge>+2</Badge>
            </div>
          </div>
        </div>
      </Panel>

      <div className="grid items-center gap-3 xl:grid-cols-[0.8fr_0.8fr_1fr_auto]">
        <button className="vse-select-button">Sort by Best Score (High to Low) <Icon name="chevronDown" className="h-4 w-4" /></button>
        <button className="vse-select-button">Match Type All (Direct + Implied) <Icon name="chevronDown" className="h-4 w-4" /></button>
        <div className="flex items-center gap-4 rounded-[16px] border border-[var(--vse-border)] bg-white px-4 py-3">
          <div className="text-sm text-[var(--vse-muted)]">Min Score</div>
          <div className="rounded-xl border border-[var(--vse-border)] px-3 py-1 text-[0.96rem] text-[var(--vse-ink)]">0.50</div>
          <input type="range" min={0} max={100} defaultValue={55} className="h-2 flex-1 accent-[var(--vse-accent)]" />
          <div className="rounded-xl border border-[var(--vse-border)] px-3 py-1 text-[0.96rem] text-[var(--vse-ink)]">1.00</div>
        </div>
        <div className="flex items-center justify-end gap-2">
          <span className="text-sm text-[var(--vse-muted)]">View</span>
          <button className="vse-icon-button !h-11 !w-11 !rounded-xl !bg-[var(--vse-soft)] !text-[var(--vse-accent)]"><Icon name="list" /></button>
          <button className="vse-icon-button !h-11 !w-11 !rounded-xl"><Icon name="grid" /></button>
        </div>
      </div>

      <div className="space-y-3">
        {hits.slice(0, 5).map((hit, index) => (
          <Panel key={hit.ticket_id} className="px-5 py-4">
            <div className="grid gap-4 xl:grid-cols-[48px_1fr_260px_110px_120px_30px] xl:items-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-full border border-[var(--vse-border)] text-lg text-[var(--vse-muted)]">{index + 1}</div>
              <div>
                <div className="flex flex-wrap items-center gap-4">
                  <a href="#" className="text-[1.12rem] font-semibold text-[var(--vse-link)] underline-offset-4 hover:underline">{hit.ticket_id}</a>
                  <div className="text-[1.22rem] font-semibold text-[var(--vse-ink)]">{hit.title}</div>
                </div>
                <p className="mt-1 max-w-[940px] text-[0.96rem] leading-7 text-[var(--vse-muted)]">{hit.summary_preview}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {(hit.direct_vs_names ?? []).concat(hit.implied_vs_names ?? []).slice(0, 3).map((name) => <Badge key={name}>{name}</Badge>)}
              </div>
              <div>
                <div className="text-[1.6rem] font-semibold tracking-[-0.03em] text-[var(--vse-accent)]">{(hit.best_score ?? 0).toFixed(2)}</div>
                <div className="text-sm text-[var(--vse-muted)]">{index < 3 ? 'Direct Match' : 'Implied Match'}</div>
              </div>
              <div className="text-[0.96rem] text-[var(--vse-ink)]">{index === 0 ? 'Mar 12, 2024' : index === 1 ? 'Jan 24, 2024' : index === 2 ? 'Dec 18, 2023' : index === 3 ? 'Nov 02, 2023' : 'Sep 14, 2023'}</div>
              <div className="text-[var(--vse-muted)]"><Icon name="chevronRight" className="h-5 w-5" /></div>
            </div>
          </Panel>
        ))}
      </div>

      <div className="flex items-center justify-between px-2 text-[0.95rem] text-[var(--vse-muted)]">
        <div>Showing 1–5 of 256 results</div>
        <div className="flex items-center gap-2">
          {['‹', '1', '2', '3', '…', '52', '›'].map((item) => (
            <button key={item} className={cn('inline-flex h-10 min-w-10 items-center justify-center rounded-xl border border-[var(--vse-border)] px-3', item === '1' && 'border-[var(--vse-accent)] text-[var(--vse-accent)]')}>
              {item}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function MergedCandidatesView({ candidates }: { candidates: Candidate[] }) {
  const mergedCount = candidates.filter((candidate) => bucketLabel(candidate).includes('merged')).length;
  const semanticOnlyCount = candidates.filter((candidate) => bucketLabel(candidate).includes('semantic')).length;
  const historicalOnlyCount = candidates.filter((candidate) => bucketLabel(candidate).includes('historical')).length;

  return (
    <div className="space-y-4 px-4 py-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Merged Candidate Ranking</h2>
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
        </div>
        <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">Ranked value stream candidates after combining semantic and historical signals.</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_280px]">
        <Panel className="grid gap-4 px-5 py-5 xl:grid-cols-4">
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Total Merged Candidates</div>
            <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">16</div>
          </div>
          <div>
            <div className="text-sm text-[var(--vse-muted)]">From Semantic (VS Search)</div>
            <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{semanticOnlyCount}</div>
          </div>
          <div>
            <div className="text-sm text-[var(--vse-muted)]">From Historical (RAG)</div>
            <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{historicalOnlyCount}</div>
          </div>
          <div>
            <div className="text-sm text-[var(--vse-muted)]">From Both Sources</div>
            <div className="mt-2 text-[2.1rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">{mergedCount}</div>
          </div>
        </Panel>

        <Panel className="px-5 py-5">
          <div className="text-sm font-semibold text-[var(--vse-ink)]">Source Legend</div>
          <div className="mt-4 space-y-3">
            <div className="flex items-center justify-between gap-3"><Badge tone="mint">merged</Badge><span className="text-sm text-[var(--vse-muted)]">From both sources</span></div>
            <div className="flex items-center justify-between gap-3"><Badge tone="blue">semantic only</Badge><span className="text-sm text-[var(--vse-muted)]">From semantic only</span></div>
            <div className="flex items-center justify-between gap-3"><Badge tone="violet">historical only</Badge><span className="text-sm text-[var(--vse-muted)]">From historical only</span></div>
          </div>
        </Panel>
      </div>

      <Panel className="overflow-hidden">
        <div className="grid min-w-[1100px] grid-cols-[80px_2fr_130px_140px_150px_1.7fr_40px] border-b border-[var(--vse-border)] px-4 py-4 text-sm text-[var(--vse-muted)]">
          <div>Rank</div>
          <div>Value Stream Candidate</div>
          <div>Final Score</div>
          <div>Source</div>
          <div>Support (Signals)</div>
          <div>Rationale</div>
          <div />
        </div>

        {candidates.slice(0, 6).map((candidate, index) => {
          const source = bucketLabel(candidate);
          return (
            <div key={candidate.entity_id} className="grid min-w-[1100px] grid-cols-[80px_2fr_130px_140px_150px_1.7fr_40px] items-center border-b border-[var(--vse-border)] px-4 py-5 last:border-b-0">
              <div className="text-[1rem] text-[var(--vse-ink)]">{index + 1}</div>
              <div className="text-[1.16rem] font-semibold text-[var(--vse-ink)]">
                {candidate.entity_name}
                <span className="ml-2 font-normal text-[var(--vse-muted)]">({candidate.entity_id})</span>
              </div>
              <div><Badge tone="mint">{scoreOf(candidate).toFixed(2)}</Badge></div>
              <div><Badge tone={sourceTone(source)}>{source}</Badge></div>
              <div className="text-center">
                <div className="text-[1rem] text-[var(--vse-ink)]">{supportCountOf(candidate)}</div>
                <div className="mt-1 text-sm text-[var(--vse-muted)]">
                  ({candidate.from_semantic ? Math.max(0, Math.round(supportCountOf(candidate) * 0.6)) : 0} semantic, {candidate.from_historical ? Math.max(0, Math.round(supportCountOf(candidate) * 0.4)) : 0} historical)
                </div>
              </div>
              <div className="text-[0.96rem] leading-7 text-[var(--vse-muted)]">{candidate.historical_reasons?.[0] ?? 'Merged rationale unavailable.'}</div>
              <div className="text-[var(--vse-muted)]"><Icon name="chevronRight" className="h-5 w-5" /></div>
            </div>
          );
        })}
      </Panel>

      <button className="flex w-full items-center justify-between rounded-[18px] border border-[var(--vse-border)] bg-white px-6 py-5 text-left text-[1rem] text-[var(--vse-muted)] shadow-[0_8px_20px_rgba(15,23,42,0.03)]">
        <span>View remaining 10 candidates</span>
        <Icon name="chevronDown" className="h-5 w-5" />
      </button>
    </div>
  );
}

function ReferenceView({ canonical, selectedCard }: { canonical: CanonicalVS[]; selectedCard?: IdeaCard }) {
  return (
    <div className="grid gap-4 px-4 py-4 xl:grid-cols-[1.35fr_0.9fr]">
      <div className="space-y-4">
        <Panel className="p-5">
          <h2 className="text-[1.8rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Idea Card Summary</h2>
          <div className="mt-6 grid gap-6 xl:grid-cols-[260px_1fr]">
            <div className="grid gap-y-3 text-[0.98rem]">
              <div className="grid grid-cols-[96px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Ticket Key</span><span className="text-[var(--vse-ink)]">{selectedCard?.doc_id ?? 'IDMT-19761'}</span></div>
              <div className="grid grid-cols-[96px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Title</span><span className="text-[var(--vse-ink)]">{selectedCard?.display_name ?? 'Intelligent Delivery Platform'}</span></div>
              <div className="grid grid-cols-[96px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Created</span><span className="text-[var(--vse-ink)]">May 8, 2024</span></div>
              <div className="grid grid-cols-[96px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Owner</span><span className="text-[var(--vse-ink)]">Manisha Reddy Chargall</span></div>
              <div className="grid grid-cols-[96px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Source</span><span className="text-[var(--vse-ink)]">Idea Management</span></div>
            </div>
            <div>
              <div className="text-[0.98rem] text-[var(--vse-muted)]">Description</div>
              <p className="mt-3 max-w-[720px] text-[1rem] leading-8 text-[var(--vse-ink)]">
                Propose a unified delivery platform to automate intake, triage, assignment, and status tracking for enterprise requests. Includes workflow orchestration, provider data management, and insights dashboards to improve cycle time and visibility.
              </p>
              <button className="mt-5 inline-flex items-center gap-2 text-[0.98rem] font-medium text-[var(--vse-accent)]">
                View full idea card
                <Icon name="link" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </Panel>

        <Panel className="p-5">
          <div className="flex items-center gap-2">
            <h3 className="text-[1.6rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Canonical Value Stream Reference</h3>
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--vse-border)] text-[11px] text-[var(--vse-muted)]">i</span>
          </div>

          <div className="vse-field mt-5">
            <Icon name="search" className="h-4 w-4 text-[var(--vse-muted)]" />
            <input className="vse-input-reset" placeholder="Search canonical value streams..." />
          </div>

          <div className="mt-5 overflow-hidden rounded-[18px] border border-[var(--vse-border)]">
            <div className="grid grid-cols-[1.3fr_90px_140px_1.8fr_40px] bg-[var(--vse-soft)] px-4 py-3 text-sm text-[var(--vse-muted)]">
              <div>Name</div>
              <div>ID</div>
              <div>Category</div>
              <div>Description</div>
              <div />
            </div>
            {canonical.map((item) => (
              <div key={item.id} className="grid grid-cols-[1.3fr_90px_140px_1.8fr_40px] items-center border-t border-[var(--vse-border)] px-4 py-4">
                <div className="text-[1rem] font-semibold text-[var(--vse-ink)]">{item.name}</div>
                <div className="text-[0.96rem] text-[var(--vse-muted)]">{item.id}</div>
                <div className="text-[0.96rem] text-[var(--vse-muted)]">{item.category}</div>
                <div className="text-[0.96rem] leading-7 text-[var(--vse-muted)]">
                  {item.name === 'Automated Claim Intake'
                    ? 'Automates intake and validation of member claims from multiple channels.'
                    : item.name === 'Provider Data Management'
                    ? 'Maintains provider data accuracy and supports lifecycle management.'
                    : item.name === 'Prior Authorization Orchestration'
                    ? 'Routes and manages prior authorization requests across products and channels.'
                    : item.name === 'Member Onboarding Experience'
                    ? 'Streamlines member onboarding and welcome communication workflows.'
                    : 'Verifies member eligibility and benefits in real time.'}
                </div>
                <div className="text-[var(--vse-muted)]"><Icon name="chevronRight" className="h-5 w-5" /></div>
              </div>
            ))}
          </div>

          <button className="mt-5 inline-flex items-center gap-2 text-[0.98rem] font-medium text-[var(--vse-accent)]">
            View full canonical value stream library
            <Icon name="link" className="h-4 w-4" />
          </button>
        </Panel>
      </div>

      <Panel className="p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-[1.6rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Reference Notes</h3>
          <button className="vse-button-secondary !px-4 !py-2"><Icon name="edit" />Edit</button>
        </div>

        <div className="mt-6">
          <div className="text-sm font-semibold text-[var(--vse-ink)]">Notes</div>
          <div className="mt-3 rounded-[18px] border border-[var(--vse-border)] bg-[var(--vse-soft)] p-5 text-[1rem] leading-8 text-[var(--vse-ink)]">
            High alignment with intake automation and provider data management flows. Consider integration patterns with authorization and onboarding.
          </div>
        </div>

        <div className="mt-6">
          <div className="text-sm font-semibold text-[var(--vse-ink)]">Tags</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {['Intake', 'Automation', 'Provider Data', 'Workflow'].map((tag) => <Badge key={tag}>{tag}</Badge>)}
          </div>
        </div>

        <div className="mt-8 border-t border-[var(--vse-border)] pt-6">
          <div className="text-[1.2rem] font-semibold text-[var(--vse-ink)]">Reference Metadata</div>
          <div className="mt-5 grid gap-y-4 text-[0.98rem]">
            <div className="grid grid-cols-[140px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Pipeline Run</span><span className="text-[var(--vse-ink)]">May 8, 2024 10:42 AM</span></div>
            <div className="grid grid-cols-[140px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Run ID</span><span className="text-[var(--vse-ink)]">c7b8f2e4-5d61-4b1c-a1d6-6bd2e3b9c6a1</span></div>
            <div className="grid grid-cols-[140px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Reference Version</span><span className="text-[var(--vse-ink)]">2024.05.08</span></div>
            <div className="grid grid-cols-[140px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Canonical Library</span><span className="text-[var(--vse-ink)]">HCSC Canonical VS Library</span></div>
            <div className="grid grid-cols-[140px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Last Updated</span><span className="text-[var(--vse-ink)]">May 8, 2024 9:15 AM</span></div>
            <div className="grid grid-cols-[140px_1fr] gap-4"><span className="text-[var(--vse-muted)]">Updated By</span><span className="text-[var(--vse-ink)]">system</span></div>
          </div>
        </div>
      </Panel>
    </div>
  );
}

function RawView({ raw }: { raw: unknown }) {
  const pretty = JSON.stringify(raw, null, 2);

  return (
    <div className="space-y-4 px-4 py-4">
      <div>
        <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Raw Output</h2>
        <p className="mt-2 text-[1rem] text-[var(--vse-muted)]">Inspect the raw data produced at each stage of the pipeline.</p>
      </div>

      <Panel className="grid gap-4 px-6 py-5 xl:grid-cols-4">
        <div className="flex items-center gap-4">
          <Icon name="clock" className="h-8 w-8 text-[var(--vse-muted)]" />
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Run Time</div>
            <div className="mt-1 text-[1rem] text-[var(--vse-ink)]">May 8, 2024, 10:42:31 AM CDT</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <Icon name="file" className="h-8 w-8 text-[var(--vse-muted)]" />
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Source Doc ID</div>
            <div className="mt-1 text-[1rem] text-[var(--vse-ink)]">IDMT-19761</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <Icon name="users" className="h-8 w-8 text-[var(--vse-muted)]" />
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Candidates Retrieved</div>
            <div className="mt-1 text-[1rem] text-[var(--vse-ink)]">20</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <Icon name="check" className="h-8 w-8 text-[var(--vse-muted)]" />
          <div>
            <div className="text-sm text-[var(--vse-muted)]">Selected Candidates</div>
            <div className="mt-1 text-[1rem] text-[var(--vse-ink)]">4</div>
          </div>
        </div>
      </Panel>

      <Panel className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--vse-border)] px-4 pt-4">
          <div className="flex gap-8">
            {['Pipeline Response', 'Retrieval Payload', 'Selection Output'].map((label, index) => (
              <button key={label} className={cn('relative pb-4 text-[0.98rem]', index === 0 ? 'font-semibold text-[var(--vse-accent)]' : 'text-[var(--vse-muted)]')}>
                {label}
                {index === 0 && <span className="absolute inset-x-0 bottom-0 h-[3px] rounded-full bg-[var(--vse-accent)]" />}
              </button>
            ))}
          </div>
          <div className="flex gap-3 pb-3">
            <button className="vse-button-secondary !px-4 !py-2"><Icon name="copy" />Copy</button>
            <button className="vse-button-secondary !px-4 !py-2"><Icon name="download" />Download <Icon name="chevronDown" className="h-4 w-4" /></button>
          </div>
        </div>
        <pre className="vse-code-block">{pretty}</pre>
      </Panel>
    </div>
  );
}

export default function Home() {
  const [cards, setCards] = useState<IdeaCard[]>([]);
  const [selectedCardDocId, setSelectedCardDocId] = useState('');
  const [cardSearch, setCardSearch] = useState('');
  const [custom, setCustom] = useState(false);
  const [query, setQuery] = useState('');
  const [count, setCount] = useState(20);

  const [step, setStep] = useState<Step>('idle');
  const [stepLabel, setStepLabel] = useState('');
  const [stepOnError, setStepOnError] = useState<Step>('idle');
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [tab, setTab] = useState<Tab>('selection');

  const currentPipelineStepRef = useRef<Step>('idle');

  useEffect(() => {
    fetch(`${API}/api/idea-cards`)
      .then((response) => response.json())
      .then((data) => {
        const nextCards: IdeaCard[] = data.cards ?? [];
        setCards(nextCards);
        if (nextCards.length) setSelectedCardDocId(nextCards[0].doc_id);
      })
      .catch(() => {});
  }, []);

  const selectedCard = useMemo(
    () => cards.find((card) => card.doc_id === selectedCardDocId),
    [cards, selectedCardDocId],
  );

  const filteredCards = useMemo(() => {
    const needle = cardSearch.trim().toLowerCase();
    if (!needle) return cards;
    return cards.filter(
      (card) =>
        card.doc_id.toLowerCase().includes(needle) ||
        card.display_name.toLowerCase().includes(needle),
    );
  }, [cardSearch, cards]);

  const selectedTicketId = useMemo(() => {
    const value = String(selectedCard?.doc_id ?? '').trim().toUpperCase();
    return TICKET_KEY_RE.test(value) ? value : '';
  }, [selectedCard]);

  const semanticCandidates = useMemo(
    () => result?.semantic_candidate_value_streams?.length ? result.semantic_candidate_value_streams : SAMPLE_SEMANTIC,
    [result],
  );
  const historicCandidates = useMemo(
    () => result?.historical_candidate_value_streams?.length ? result.historical_candidate_value_streams : SAMPLE_HISTORIC,
    [result],
  );
  const faissHits = useMemo(
    () => result?.historical_ticket_hits?.length ? result.historical_ticket_hits : SAMPLE_FAISS,
    [result],
  );
  const mergedCandidates = useMemo(
    () =>
      result?.merged_candidate_value_streams?.length
        ? result.merged_candidate_value_streams
        : result?.candidate_value_streams?.length
        ? result.candidate_value_streams
        : SAMPLE_MERGED,
    [result],
  );
  const selectedValueStreams = useMemo(
    () => result?.selected_value_streams?.length ? result.selected_value_streams : SAMPLE_SELECTED,
    [result],
  );
  const rejectedValueStreams = useMemo(
    () => result?.rejected_candidates?.length ? result.rejected_candidates : SAMPLE_REJECTED,
    [result],
  );
  const canonicalValueStreams = useMemo(
    () => result?.canonical_value_streams?.length ? result.canonical_value_streams : SAMPLE_CANONICAL,
    [result],
  );
  const rawPayload = result?.raw_response ?? result ?? SAMPLE_RAW;

  const busy = !['idle', 'done', 'error'].includes(step);
  const topStatus = err ? 'Error' : step === 'done' ? 'Idle' : statusLabel(step);

  async function runPipeline() {
    setErr(null);
    setResult(null);
    setStep('idle');
    setStepLabel('');
    currentPipelineStepRef.current = 'idle';

    try {
      const body: Record<string, unknown> = {
        top_k_value_streams: count,
        top_k_historical: count,
        use_llm_finalizer: true,
      };

      if (custom && query.trim()) {
        body.idea_card_text = query.trim();
      } else if (selectedTicketId) {
        body.ticket_id = selectedTicketId;
      } else {
        throw new Error('Selected card is not a valid ticket key. Use Custom Text for local idea-card entries.');
      }

      const response = await fetch(`${API}/rag/value-streams/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const failure = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(failure.detail ?? 'Pipeline request failed.');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response stream returned by the API.');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          if (!frame.trim()) continue;

          const lines = frame.split('\n');
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
            setStep('done');
            setStepLabel('');
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
    } catch (error: unknown) {
      setStepOnError(currentPipelineStepRef.current);
      setStep('error');
      setErr(error instanceof Error ? error.message : String(error));
    }
  }

  function clearPipeline() {
    setErr(null);
    setResult(null);
    setStep('idle');
    setStepLabel('');
    setTab('selection');
  }

  async function copyRaw() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(rawPayload, null, 2));
    } catch {
      setErr('Unable to copy raw output to the clipboard.');
    }
  }

  function exportRaw() {
    const blob = new Blob([JSON.stringify(rawPayload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'value-stream-explorer-output.json';
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="vse-app">
      <header className="border-b border-[var(--vse-border)] bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1720px] flex-col gap-4 px-6 py-5 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-5">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[linear-gradient(180deg,#0d2c45_0%,#081d31_100%)] text-[1.4rem] font-semibold tracking-tight text-white">HCSC</div>
            <div className="flex items-center gap-4">
              <h1 className="text-[2.15rem] font-semibold tracking-[-0.04em] text-[var(--vse-ink)]">Value Stream Explorer</h1>
              <span className="rounded-2xl border border-[var(--vse-border)] bg-[var(--vse-soft)] px-3 py-1 text-[1rem] text-[var(--vse-muted)]">v2.4</span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button className="vse-select-button min-w-[136px] justify-between">
              <span className="inline-flex items-center gap-3">
                <span className="h-3.5 w-3.5 rounded-full bg-[var(--vse-accent)]" />
                {topStatus}
              </span>
              <Icon name="chevronDown" className="h-4 w-4" />
            </button>
            <button onClick={runPipeline} disabled={busy || (!custom && !selectedTicketId) || (custom && !query.trim())} className="vse-button-primary disabled:cursor-not-allowed disabled:opacity-50">
              <Icon name="play" />
              {busy ? stepLabel || 'Running...' : 'Run Pipeline'}
            </button>
            <button onClick={clearPipeline} className="vse-button-secondary"><Icon name="refresh" />Clear</button>
            <button onClick={exportRaw} className="vse-button-secondary"><Icon name="download" />Export</button>
            <button className="vse-icon-button"><Icon name="dots" /></button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1720px] gap-0 xl:grid-cols-[370px_minmax(0,1fr)]">
        <aside className="border-r border-[var(--vse-border)] px-5 py-5">
          <div className="rounded-[22px] border border-[var(--vse-border)] bg-white p-3 shadow-[0_18px_40px_rgba(15,23,42,0.04)]">
            <div className="grid grid-cols-2 gap-2 rounded-[18px] bg-[var(--vse-soft)] p-1">
              <button
                onClick={() => setCustom(false)}
                className={cn('rounded-[14px] px-3 py-3 text-[1rem] font-medium', !custom ? 'bg-white text-[var(--vse-ink)] shadow-[0_8px_24px_rgba(15,23,42,0.06)]' : 'text-[var(--vse-muted)]')}
              >
                Idea Card
              </button>
              <button
                onClick={() => setCustom(true)}
                className={cn('rounded-[14px] px-3 py-3 text-[1rem] font-medium', custom ? 'bg-white text-[var(--vse-ink)] shadow-[0_8px_24px_rgba(15,23,42,0.06)]' : 'text-[var(--vse-muted)]')}
              >
                Custom Text
              </button>
            </div>

            <div className="mt-4 rounded-[18px] border border-[var(--vse-border)] bg-[linear-gradient(180deg,#ffffff_0%,#fbfcfe_100%)] p-5">
              {!custom ? (
                <div className="flex min-h-[210px] flex-col items-center justify-center text-center">
                  <div className="mb-5 text-[var(--vse-accent)]"><Icon name="upload" className="h-12 w-12" /></div>
                  <div className="text-[1.6rem] font-semibold tracking-[-0.03em] text-[var(--vse-accent)]">Upload Idea Card</div>
                  <div className="mt-4 text-[1rem] leading-7 text-[var(--vse-muted)]">Drag & drop PPT/PDF or click to browse</div>
                  <div className="mt-3 text-[0.95rem] text-[var(--vse-muted)]">Max 50MB per file</div>
                </div>
              ) : (
                <div>
                  <div className="text-[1.2rem] font-semibold text-[var(--vse-ink)]">Paste Idea Text</div>
                  <textarea
                    rows={9}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Paste the idea card summary, business need, or Jira description..."
                    className="mt-4 w-full resize-none rounded-[16px] border border-[var(--vse-border)] bg-[var(--vse-soft)] px-4 py-4 text-[0.98rem] leading-7 text-[var(--vse-ink)] outline-none focus:border-[var(--vse-accent-soft)] focus:bg-white"
                  />
                </div>
              )}
            </div>

            <div className="mt-5">
              <div className="text-[1rem] font-semibold text-[var(--vse-ink)]">Selected Idea Card</div>
              <div className="vse-field mt-3">
                <input
                  value={cardSearch}
                  onChange={(event) => setCardSearch(event.target.value)}
                  className="vse-input-reset"
                  placeholder="Search idea cards..."
                />
                <Icon name="chevronDown" className="h-4 w-4 text-[var(--vse-muted)]" />
              </div>

              <div className="mt-3 space-y-3">
                {(filteredCards.length ? filteredCards : [{ doc_id: 'IDMT-19761', display_name: 'Intelligent Delivery Platform', has_mapping: true } as IdeaCard])
                  .slice(0, 4)
                  .map((card) => (
                    <button
                      key={card.doc_id}
                      onClick={() => {
                        setSelectedCardDocId(card.doc_id);
                        setCustom(false);
                      }}
                      className={cn(
                        'flex w-full items-center justify-between rounded-[16px] border px-4 py-4 text-left transition',
                        selectedCardDocId === card.doc_id || (!selectedCardDocId && card.doc_id === 'IDMT-19761')
                          ? 'border-[var(--vse-accent-soft)] bg-white shadow-[0_12px_24px_rgba(15,23,42,0.04)]'
                          : 'border-[var(--vse-border)] bg-white hover:border-[var(--vse-accent-soft)]',
                      )}
                    >
                      <span className="flex items-center gap-3">
                        <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[var(--vse-accent-soft)] bg-[var(--vse-soft)] text-[var(--vse-accent)]">
                          <Icon name="file" className="h-4 w-4" />
                        </span>
                        <span className="text-[1rem] font-medium text-[var(--vse-ink)]">{card.doc_id}</span>
                      </span>
                      <Icon name="dots" className="h-4 w-4 text-[var(--vse-muted)]" />
                    </button>
                  ))}
              </div>
            </div>

            <Panel className="mt-4 p-4">
              <div className="text-[1.3rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Details</div>
              <div className="mt-4 space-y-3 text-[0.98rem]">
                <div className="grid grid-cols-[100px_1fr] gap-3"><span className="text-[var(--vse-muted)]">Ticket Key</span><span className="text-[var(--vse-ink)]">{selectedCard?.doc_id ?? 'IDMT-19761'}</span></div>
                <div className="grid grid-cols-[100px_1fr] gap-3"><span className="text-[var(--vse-muted)]">Title</span><span className="text-[var(--vse-ink)]">{selectedCard?.display_name ?? 'Intelligent Delivery Platform'}</span></div>
                <div className="grid grid-cols-[100px_1fr] gap-3"><span className="text-[var(--vse-muted)]">Created</span><span className="text-[var(--vse-ink)]">May 8, 2024</span></div>
                <div className="grid grid-cols-[100px_1fr] gap-3"><span className="text-[var(--vse-muted)]">Owner</span><span className="text-[var(--vse-ink)]">Manisha Reddy Chargall</span></div>
                <div className="grid grid-cols-[100px_1fr] gap-3"><span className="text-[var(--vse-muted)]">Source</span><span className="text-[var(--vse-ink)]">Idea Management</span></div>
              </div>
            </Panel>

            <Panel className="mt-4 p-4">
              <div className="text-[1.3rem] font-semibold tracking-[-0.03em] text-[var(--vse-ink)]">Configuration</div>
              <div className="mt-5 flex items-center justify-between">
                <div className="text-[1rem] font-medium text-[var(--vse-ink)]">Historical RAG Candidates</div>
                <div className="rounded-2xl border border-[var(--vse-border)] px-4 py-2 text-[1rem] text-[var(--vse-ink)]">{count}</div>
              </div>
              <div className="mt-5">
                <input type="range" min={1} max={50} value={count} onChange={(event) => setCount(Number(event.target.value))} className="h-2 w-full accent-[var(--vse-accent)]" />
                <div className="mt-2 flex justify-between text-[0.96rem] text-[var(--vse-muted)]"><span>1</span><span>50</span></div>
                <p className="mt-4 text-[0.96rem] leading-7 text-[var(--vse-muted)]">Select the number of historical value-stream candidates to retrieve before ranking.</p>
              </div>
              <button onClick={runPipeline} disabled={busy || (!custom && !selectedTicketId) || (custom && !query.trim())} className="vse-button-secondary mt-6 w-full justify-center !py-4 disabled:cursor-not-allowed disabled:opacity-50">
                <Icon name="spark" />
                Run Historical RAG
              </button>
              {!custom && !selectedTicketId && (
                <p className="mt-3 text-sm leading-6 text-[#b46a10]">The selected item is a local idea-card entry. Use Custom Text or select a valid ticket key to run the pipeline.</p>
              )}
            </Panel>
          </div>
        </aside>

        <main className="px-5 py-5">
          <div className="space-y-4">
            <Panel className="p-4">
              <div className="flex flex-col gap-4 xl:flex-row xl:items-stretch xl:justify-between">
                <div className="flex flex-1 flex-wrap items-stretch gap-3">
                  {PIPELINE_STEPS.map((item, index) => (
                    <StageCard
                      key={item.key}
                      title={item.title}
                      subtitle={item.key === step && stepLabel ? stepLabel : item.subtitle}
                      icon={item.icon}
                      status={stageStatus(step, item.key, stepOnError)}
                      connector={index < PIPELINE_STEPS.length - 1}
                    />
                  ))}
                </div>
                <div className="min-w-[210px] rounded-[18px] border border-[var(--vse-border)] bg-white px-5 py-4 shadow-[0_12px_24px_rgba(15,23,42,0.03)]">
                  <div className="flex items-center gap-3 text-[1rem] font-medium text-[var(--vse-ink)]">
                    <span className={cn('h-3.5 w-3.5 rounded-full', err ? 'bg-[#d0473f]' : busy ? 'bg-[var(--vse-accent)]' : 'bg-[var(--vse-accent)]')} />
                    {topStatus}
                  </div>
                  <div className="mt-4 text-[0.98rem] leading-7 text-[var(--vse-muted)]">
                    {err ? err : busy ? 'Pipeline is currently running.' : 'Pipeline is ready to run.'}
                  </div>
                </div>
              </div>
            </Panel>

            <Panel className="overflow-hidden">
              <TabNav tab={tab} onChange={setTab} />

              {tab === 'selection' && <SelectionView selected={selectedValueStreams} rejected={rejectedValueStreams} />}
              {tab === 'comparison' && <ComparisonView candidates={mergedCandidates} />}
              {tab === 'retrieval' && <RetrievalView />}
              {tab === 'vsCandidates' && <VsCandidatesView candidates={semanticCandidates} />}
              {tab === 'historicCandidates' && <HistoricCandidatesView candidates={historicCandidates} />}
              {tab === 'faissHits' && <FaissHitsView hits={faissHits} />}
              {tab === 'mergedCandidates' && <MergedCandidatesView candidates={mergedCandidates} />}
              {tab === 'reference' && <ReferenceView canonical={canonicalValueStreams} selectedCard={selectedCard} />}
              {tab === 'raw' && <RawView raw={rawPayload} />}
            </Panel>

            <div className="flex flex-wrap items-center justify-between gap-3 px-1">
              {result && (
                <div className="text-[0.96rem] text-[var(--vse-muted)]">
                  Loaded live pipeline results for <span className="font-medium text-[var(--vse-ink)]">{result.source_doc_id ?? selectedTicketId ?? 'current idea card'}</span>.
                </div>
              )}
              {!result && (
                <div className="text-[0.96rem] text-[var(--vse-muted)]">
                  Showing reference-style preview content until a live pipeline run completes.
                </div>
              )}
              <div className="flex gap-3">
                <button onClick={copyRaw} className="vse-button-secondary !px-4 !py-2"><Icon name="copy" />Copy Raw</button>
                <button onClick={exportRaw} className="vse-button-secondary !px-4 !py-2"><Icon name="download" />Export JSON</button>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
