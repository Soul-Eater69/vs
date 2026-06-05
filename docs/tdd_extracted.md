TECHNICAL DESIGN DOCUMENT
Theme & Epic Generation
Ingestion and Data Science Solution
Contents
1.  Executive Summary
2.  Source of Truth and Data Ownership
3.  Ingestion Design Flow
4.  Target Storage and Unified Index Schema
5.  Data Science Solution
5.1  Condense Step
5.2  Ticket Context Extraction
5.3  Unified Index Retrieval Lanes
5.4  Candidate Merge, Ranking and Review Pool
5.5  LLM Selection Prompt and Output
5.6  Quality Metrics for Value Stream Selection
6.  Theme Generation Solution
6.1  Orchestration Sequence
6.2  Context Passed to Each Generation Call
6.3  Final Theme Package Returned for Review
1. Executive Summary
SMEs and Business Architects analyze IDMT Engagement Requests, review idea cards and attachments, select approved Value Streams and Stages, and create Jira Themes, Epics and capability hierarchies. The solution accelerates that workflow by producing a structured, evidence-backed recommendation package for SME review.
The design has two modules: an ingestion module that converts historical IDMT tickets into a trusted corpus with lineage and ground-truth labels, and a data science module that uses governed catalogues plus historical evidence to recommend Value Streams, Stages, Theme content, Business Needs, and L2/L3 capabilities. The SME remains the final approver; Jira remains the system of record for approved artifacts.
2. Source of Truth and Data Ownership
The table below captures authoritative inputs, target stores and value added by each curated asset. idp_idmt_data is the sole AI Search index; different entity types are separated by metadata and document style.
Entity
Source of Truth
Destination
Extraction / Processing
Engagement Request (ER)
Jira API + iTech DB audit
Cosmos historic IDMT document + idp_idmt_data
iTech DB identifies eligible ERs and Theme links. Jira API fetches description, attachments and Theme data. Extraction, LLM summary, VS fuzzy match and direct/implied classification enrich the record.
Value Stream
Sightline
Cosmos catalogue + idp_idmt_data valueStream document
dedupe, JSON organization, metadata tagging and ingestion into the unified search index.
Value Stage, L2, L3
Sightline
Cosmos catalogue;
organize, version and ingest.
Theme details
Jira API
Cosmos historic IDMT document;
Theme description text extraction;
3. Ingestion Design Flow
End-to-end ingestion sequence
Figure 1. End-to-end ingestion flow using iTech DB for eligibility audit and idp_idmt_data as the unified AI Search index.
The ingestion module starts by identifying usable historical tickets instead of ingesting every IDMT ticket. The iTech DB audit finds Engagement Requests after 2023-01-01, checks Theme links, confirms issue type, and limits ingestion to records with valid or partially valid approved Value Stream ground truth. The selected records are then fetched from Jira, enriched, persisted in Cosmos, and indexed into idp_idmt_data.
•   Attachment extraction prioritizes the likely idea card: PPT/PPTX first, then PDF, then DOC/DOCX. The top four supported attachments are extracted.
•   PPT is prioritized because SMEs confirmed it is the most common idea-card format.
•   The consolidated context is capped before LLM calls to reduce noise and control token usage.
•   Only approved Value Stream and Stage names pass validators; invalid or unsupported labels are excluded from ground-truth training data.
4. Target Storage and Unified Index Schema
Cosmos stores durable source lineage, historical ground truth, catalogue metadata and future SME feedback. idp_idmt_data is the unified AI Search index. It contains multiple document styles separated by entityType and metadata: historical IDMT documents and Value Stream catalogue documents.
4.1 Cosmos historic IDMT document - top-level fields
Field
Description
id
Document id / internal key
source
Origin system, e.g., Jira
sourceId
Source ticket key, e.g., IDMT-####
entityType
Entity category, e.g., EngagementRequest
parentId
Parent entity id where applicable
parentEntityType
Parent entity type where applicable
createdAt
Source creation timestamp
createdBy
Source creator / actor
modifiedAt
Source modification timestamp
modifiedBy
Source modifier / actor
properties
Nested object containing extracted business context and theme ground truth
4.2 Cosmos properties object
Field
Description
properties.description
Original Jira description or cleaned description
properties.summary
LLM-generated business summary
properties.rawText
Consolidated raw context from description and extracted attachments
properties.keyTerms
Domain terms and acronyms
properties.businessProblem
Business problem / pain point
properties.businessCapability
Desired capability or outcome
properties.stakeholders
Stakeholder groups
properties.systemsAndProducts
Referenced systems, platforms and products
properties.themes
Array of Theme-level ground-truth objects; expanded below
4.3 Cosmos properties.themes array object
Field
Description
groupId
Jira GROUP issue id/key for the Theme
valueStreamId
Approved Value Stream id resolved from the 50-VS catalogue
valueStreamName
Canonical approved Value Stream name
supportType
direct / implied / weak / unsupported classification against source context
reason
Short explanation for why this Value Stream is linked to the ER
evidence
Source-text snippet supporting the classification
themeDescription
Cleaned Theme description from Jira
themeTitle
Theme title / summary from Jira
4.4 idp_idmt_data - historical IDMT index document
Field
Description
id
Search document key
source
Origin system
sourceId
Source ticket key
entityType
Indexed entity category, e.g., historicalIdmtDocument
parentId
Parent entity id where applicable
parentEntityType
Parent entity type where applicable
createdAt
Source creation timestamp
createdBy
Source creator / actor
modifiedAt
Source modification timestamp
modifiedBy
Source modifier / actor
content
Top-level retrieval text assembled from curated fields; this is the text embedded for search
content_vector
Top-level embedding vector for content
properties
Nested object with extracted fields used for filtering, display and traceability
4.5 Historical index properties object
Field
Description
properties.description
Cleaned description
properties.summary
LLM-generated summary
properties.keyTerms
Extracted key terms
properties.businessProblem
Extracted business problem
properties.businessCapability
Extracted business capability
properties.stakeholders
Extracted stakeholders
properties.systemsAndProducts
Extracted systems/products
4.6 idp_idmt_data - Value Stream index document
Field
Description
id
Search document key
source
Catalogue source, e.g., Sightline
entityType
valueStream
parentId
Parent catalogue id where applicable
parentEntityType
Parent catalogue entity type where applicable
createdAt
Catalogue creation timestamp when available
createdBy
Catalogue creator / actor when available
modifiedAt
Catalogue update timestamp when available
modifiedBy
Catalogue modifier / actor when available
content
Top-level retrieval text generated from valueStreamName and valueStreamDescription
content_vector
Top-level embedding vector for content
properties
Nested Value Stream catalogue object
4.7 Value Stream properties object
Field
Description
properties.valueStreamDescription
Approved Value Stream description
properties.valueStreamName
Canonical approved Value Stream name
properties.valueStreamId
Approved Value Stream id
The historical document content vector is generated from curated structured text rather than raw Jira markup. This keeps retrieval focused on business intent and avoids boilerplate, comments and attachment noise.
[Heading] 5. Data Science Solution
Once Cosmos and idp_idmt_data are populated, the data science flow starts from a user-provided IDMT ticket id. The service first extracts the current ticket context from Jira, then searches the unified index in two parallel lanes: Value Stream catalogue documents and historical Engagement Request documents. The result is a ranked, evidence-backed Value Stream recommendation set for SME review.
Figure 2. Data science flow from IDMT ticket id to ranked Value Stream recommendations.
[Heading 2] 5.1 Condense Step
Figure 2. End-to-end data science flow: condense step, VS selection, HITL approval, and theme generation.
The condense step is the first LLM call in the data science flow. It extracts structured context from the IDMT ticket source material in a single pass and returns two groups of fields: summaryFields for retrieval, routing, and LLM context, and generationSignals for Theme Description and Business Needs generation. A single extraction pass avoids re-processing the same idea card or attachment text downstream. The output is shared across all generation steps.
Source priority
•   If an attachment named or tagged idea_card.ppt / idea_card.pptx exists, use it as the primary business context source.
•   If the idea card is missing, fall back to the Jira ticket description plus the top four supported attachments in priority order: PowerPoint, PDF, Word document.
Output fields
Group
Fields
Used by
summaryFields
generatedSummary, businessProblem, businessCapability, keyTerms, stakeholders, systemsAndProducts
VS selection, stage prediction, Theme Description, Business Needs, L2, L3
generationSignals
marketSegments, fundingModelSignals, marketOpportunity, businessSolutionObjectives, valueProposition, estimatedBenefits, dependencies, resourcesNeeded, digitalExperienceSignals, productAvailabilitySignals, planSignals, networkSignals, productPairingSignals, businessRules, operationalSignals, reportingSignals, trainingSignals, notes
Theme Description, Business Needs
Each generationSignal field is an array of lightweight evidence objects carrying text, source, and sourceSection. Empty arrays are returned when no evidence exists in the source material. Signals are never invented; if a category is absent from the source, the array is left empty.
[Heading 2] 5.2 Ticket context extraction
The user provides an IDMT ticket id. The Jira API is used to inspect the ticket attachments and locate an attachment explicitly tagged or named idea_card.ppt / idea_card.pptx. If the idea card is present, it is treated as the primary source of business context. If the idea card is not found, the fallback path summarizes the IDMT ticket description and extracts the top four supported attachments using the ingestion priority order: PPT/PPTX, PDF, then DOC/DOCX.
Both paths produce the same normalized context object: generated summary, description, extracted raw text, key terms, business problem, business capability, stakeholders, and systems/products. This keeps downstream retrieval and LLM selection consistent regardless of whether the ticket contains a formal idea card.
[Heading 2] 5.3 Unified index retrieval lanes
Lane
Index filter
Retrieval method
Output
Value Stream catalogue lane
idp_idmt_data where entityType = valueStream
Hybrid search over valueStreamName, valueStreamDescription and content/content_vector
Top 50 Value Stream candidates
Historical Engagement Request lane
idp_idmt_data where entityType = EngagementRequest
Vector similarity over content_vector for curated historical ticket context;
Top K = 6 historical tickets shown to the user
The two retrieval calls run in parallel against the same idp_idmt_data index. The user can review the six matched historical tickets and select the examples that are actually relevant. Only selected tickets are used as strong historical evidence during candidate ranking.
[Heading 2] 5.4 Candidate merge, ranking and review pool
Bucket
Definition
Ranking treatment
Semantic + Historic
Value Stream appears in both lanes: the valueStream catalogue lane and the selected historical Engagement Request evidence lane.
Highest priority. Strongest signal because the candidate has catalogue-level semantic fit plus historical BA/SME precedent.
Historic-only
Value Stream is surfaced from matched historical Engagement Requests, but does not rank strongly in the catalogue semantic lane.
Retained for implied VS recall. Included when historical evidence, support type, reason/snippet, or frequency across selected tickets is strong.
Semantic-only
Value Stream comes from the catalogue semantic lane only. It may fit the current idea, but is not strongly supported by selected historical tickets.
Kept for coverage of new or uncommon ideas. Included after stronger overlap/historic candidates up to the LLM review-pool limit.
The two retrieval lanes produce candidate Value Streams that are merged into three evidence buckets: Semantic-only, Historic-only, and Semantic + Historic. Candidates appearing in both lanes are ranked highest because they are supported both by catalogue-level semantic similarity and by historical BA-created examples. Historic-only candidates are retained to improve implied Value Stream recall, while semantic-only candidates preserve coverage for new ideas that may not have close historical precedents. Ranking uses lane overlap, catalogue score, historical-ticket score, selected-ticket evidence, support type, frequency across selected examples, and validation against the approved Value Stream registry.
[Heading 2] 5.5 LLM selection prompt and output
The Value Stream LLM is not given only a flat list of candidate names. It receives a curated review pool built from both retrieval lanes. Each candidate is represented as an evidence block so the model can compare catalogue similarity, historical precedent and user-selected ticket relevance before returning the final Value Stream selection.
Prompt inputs passed to the LLM
Ticket context:
IDMT ticket id,
generated summary,
business problem,
business capability,
key terms,
stakeholders,
systems/products
•   User control: requested number of Value Streams, defaulting to 10, plus any custom instruction that asks for more or fewer selections.
•   Candidate pool: candidate blocks grouped into Semantic + Historic, Historic-only and Semantic-only buckets.
•   Historical ticket evidence: the six matched Engagement Requests are shown to the user first; the selected/relevant tickets are used to strengthen historical evidence in the review pool.
Candidate block format used in the review pool
Each candidate is passed as a compact block instead of a raw row. This keeps the prompt readable and makes the evidence explicit.
Candidate: Configure, Price and QuotevalueStreamId: VSR-####bucket: Semantic + HistoricValue stream desc: value stream descriptionhistoric evidence: IDMT-####, supportType=implied, reason=<from historic data>, evidence=< snippet from ingested data>ranking signals: semanticRank=4, historicRank=2,
Selection and execution behavior
•   Candidates appearing in both lanes are prioritized because they have catalogue-level semantic fit and historical BA precedent.
•   Historic-only candidates are retained to protect implied Value Stream recall when the ticket does not explicitly name the VS.
•   Semantic-only candidates are retained for new ideas where there may be no close historical ticket.
•   The review pool is split into two parallel LLM calls, then merged, deduplicated and validated against the approved Value Stream catalogue.
Expected LLM output
The LLM returns a structured Value Stream selection payload. Each selected item must include:
•   valueStreamId and valueStreamName resolved to the approved catalogue.
•   confidence score
•   evidence bucket: implied or direct.
•   reason explaining the business fit in plain language.
•   source tickets: when historical support is used.
The default output is the top 10 Value Streams. A user instruction can request a smaller or larger set; final names are still validated and deduplicated before being returned.
[Heading 2] 5.6 Quality metrics for Value Stream selection
Precision is monitored on the final selected Value Streams against the curated ground-truth labels; observed range is 60-65%.
Recall is monitored against the same ground truth, observed range is 75-82%.
Stage prediction quality metrics
Precision is monitored on the selected stages against approved stage ground truth; observed range is 38–45%.
Recall is monitored against the same ground truth; observed range is 64–70%.
Note: stage prediction precision is lower than VS selection because stage catalogues are finer-grained and ground truth labels are sparser. Recall is prioritised to ensure relevant stages are not dropped before Business Needs and L2/L3 generation.
Latency is measured for the Value Stream LLM section end-to-end; the two-call parallel split currently averages about 35 seconds before merge and validation.
6. Theme Generation Solution
After Value Stream recommendation, a human-in-the-loop review first confirms the final Value Stream set. Theme generation starts only after this approval. Each approved Value Stream maps to one Theme package. For each approved Value Stream, the system generates the Theme title, standardized description, selected stages, Business Needs, and L2/L3 capability hierarchy using the normalized ticket context and governed catalogue data from Cosmos.
Figure 3. Theme generation flow after human approval of Value Streams.
6.1 Orchestration sequence
The sequence is approval-gated: no Theme description, stage prediction, Business Needs, or capability output is generated until the SME confirms the Value Stream set. After approval, the system creates one Theme context for each approved Value Stream and executes the generation pipeline for that Theme.
•   Parallel call 1 - Stage prediction: predicts the relevant Value Stages for the approved Value Stream using the Value Stream description, normalized ticket summary/key fields, and the governed stage list from Cosmos.
•   Parallel call 2 - Theme description: generates the standard Theme description in parallel with stage prediction. This call also starts only after human approval of the Value Stream set.
•   Stage-dependent fan-out: once selected stages are resolved, the system starts Business Needs, L2 capability, and L3 capability generation asynchronously.
•   Business Needs generation: explains the selected stages in the context of the IDMT ticket, business problem, stakeholders, and desired capability.
•   L2 and L3 capability generation (parallel, both wait only on stage output): once stages are resolved, L2 and L3 generation are launched as independent asynchronous calls. L2 uses the selected stages and the governed stage-to-L2 mappings from Cosmos. L3 uses the selected stages and the governed stage-to-L3 mappings from Cosmos. L3 does not depend on L2 output; both calls run in parallel from the same stage result.
•   Theme title: deterministic, built from the IDMT ticket title plus the approved Value Stream name. The final Theme package combines the deterministic title with generated description, Business Needs, selected stages, and L2/L3 capabilities.
6.2 Context passed to each generation call
All generation calls draw from the condense step output (section 5.1): summaryFields and generationSignals. Each call receives only the fields and governed catalogue slice it needs. Stage prediction and Theme Description receive the approved Value Stream from Cosmos. Business Needs, L2, and L3 additionally receive the selected stages resolved by stage prediction. Specific field lists per call are shown in the table below.
Stage Prediction
Context:
•   Approved valueStreamId, valueStreamName, valueStreamDescription
•   summaryFields: generatedSummary, businessProblem, businessCapability, keyTerms, stakeholders, systemsAndProducts
•   generationSignals: businessSolutionObjectives, dependencies, digitalExperienceSignals, operationalSignals, reportingSignals, businessRules, notes
•   Cosmos catalogue: all governed stages mapped to the approved VS (stageId, stageName, stageDescription)
Output: selectedStages array: each entry contains stageId, stageName, rank, reason, evidence, and validationStatus.
Theme Description
Context:
•   Approved valueStreamId, valueStreamName, valueStreamDescription
•   idmtTicketId, idmtTicketTitle
•   summaryFields: generatedSummary, businessProblem, businessCapability, keyTerms, stakeholders, systemsAndProducts
•   generationSignals: marketSegments, fundingModelSignals, marketOpportunity, businessSolutionObjectives, valueProposition, estimatedBenefits, dependencies, resourcesNeeded, digitalExperienceSignals, productAvailabilitySignals, planSignals, networkSignals, productPairingSignals, operationalSignals, reportingSignals, notes
•   Runs after HITL approval in parallel with stage prediction
Output: Structured Theme description: themeOverview, productAvailability (optional fields), initiativeOverview, keyFeatures, digitalExperience (optional), integrationOperationalCapabilities (optional).
Business Needs
Context:
•   Approved valueStreamId, valueStreamName, valueStreamDescription
•   Selected stages (stageId, stageName, stageDescription)
•   summaryFields: generatedSummary, businessProblem, businessCapability, keyTerms, stakeholders, systemsAndProducts
•   generationSignals: businessSolutionObjectives, dependencies, resourcesNeeded, digitalExperienceSignals, businessRules, operationalSignals, reportingSignals, trainingSignals, notes
•   Waits for stage prediction output
Output: businessNeeds array: each entry contains stageId, stageName, businessProductFeatures (featureName, numbered needs, notes, dependencies, businessRules), operationalTraining (optional), operationalReporting (optional), and validationStatus.
L2 Capabilities
Context:
•   Approved valueStreamId, valueStreamName
•   Selected stages (stageId, stageName, stageDescription)
•   Cosmos stage-to-L2 mappings: for each selected stage, the governed L2 options mapped to it
•   summaryFields: generatedSummary, keyTerms, stakeholders, systemsAndProducts
•   Waits for stage prediction output; runs in parallel with L3
Output: L2 capability list grouped by stage: each stage maps to one or more L2 capabilities, each with name, description, and reason.
L3 Capabilities
Context:
•   Approved valueStreamId, valueStreamName
•   Selected stages (stageId, stageName, stageDescription)
•   Cosmos stage-to-L3 mappings: for each selected stage, the governed L3 options mapped to it
•   summaryFields: generatedSummary, keyTerms, stakeholders, systemsAndProducts
•   Waits for stage prediction output only; does not depend on L2; runs in parallel with L2
Output: L3 capability list grouped by stage: each stage maps to one or more L3 capabilities, each with name, description, and reason.
6.3 Final Theme package returned for review
The phase returns one Theme package per approved Value Stream. The package remains a recommendation until the SME reviews and approves it for Jira writeback. The expected structured output contains:
•   themeTitle: deterministic title built from IDMT ticket title + Value Stream name.
•   themeDescription: standardized description narrative.
•   businessNeeds: stage-aware explanation of Business Needs for the selected stages and ticket context.
•   l2Capabilities and l3Capabilities