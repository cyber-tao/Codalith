import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Icon } from "../components/Icons";
import { adminRequest, errorMessage } from "./api";
import { ACTIVE_STATUSES, bytes, jobError, phaseElapsed, phasePercent, readable, RESUMABLE_STATUSES, RETRYABLE_STATUSES } from "./format";
import type { BuildJob, BuildPlan, Database, DirectoryListing, ItemList, ManagementConfig, Repository, SourceTag, SpaceUsage, TagListing } from "./types";
import { useJobEvents } from "./useJobEvents";
import { useResource } from "./useResource";

const HISTORY_POLL_MS = 5_000;
const ACTIVE_POLL_MS = 2_000;
const TAG_PAGE_SIZE = 25;

interface Props {
  view: "databases" | "build-jobs";
  token: string;
  onTokenChange: (token: string) => void;
  onNavigate: (view: "databases" | "build-jobs") => void;
}

export function ManagementConsole({ view, token, onTokenChange, onNavigate }: Props) {
  const config = useResource<ManagementConfig>("config", token);
  const repositories = useResource<ItemList<Repository>>("repositories", token);
  const databases = useResource<ItemList<Database>>("databases", token, HISTORY_POLL_MS);
  const jobs = useResource<ItemList<BuildJob>>("jobs", token, HISTORY_POLL_MS);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [notice, setNotice] = useState("");
  const error = config.error || repositories.error || databases.error || jobs.error;

  const refresh = () => {
    config.refresh();
    repositories.refresh();
    databases.refresh();
    jobs.refresh();
  };

  return (
    <main className="management-canvas" id={view}>
      <div className="management-heading">
        <div>
          <span className="management-eyebrow">Source intelligence</span>
          <h1>{view === "databases" ? "Databases" : "Build jobs"}</h1>
          <p>{view === "databases"
            ? "Import Git tags into one shared database. Each published version stays independently queryable."
            : "Follow durable build progress, inspect failures, and continue unfinished versions."}</p>
        </div>
        <div className="management-actions">
          {token ? <>
            <button className="management-button secondary" type="button" onClick={refresh}><Icon name="refresh" /> Refresh</button>
            <button className="management-button quiet" type="button" onClick={() => onTokenChange("")}>Disconnect</button>
          </> : null}
        </div>
      </div>
      <div className="management-tabs" aria-label="Database management">
        <button type="button" className={view === "databases" ? "active" : ""} onClick={() => onNavigate("databases")}><Icon name="database" /> Databases</button>
        <button type="button" className={view === "build-jobs" ? "active" : ""} onClick={() => onNavigate("build-jobs")}><Icon name="activity" /> Build jobs</button>
      </div>
      {!token ? <AdminConnect onConnect={onTokenChange} /> : <>
        {error ? <Message kind="error">{error} <button type="button" onClick={refresh}>Retry</button></Message> : null}
        {config.data?.worker_error ? <Message kind="error">Build worker: {config.data.worker_error}</Message> : null}
        {notice ? <Message>{notice}<button type="button" onClick={() => setNotice("")} aria-label="Dismiss notification"><Icon name="close" size={14} /></button></Message> : null}
        {!config.data ? <div className="management-empty">Loading management configuration…</div> : !config.data.enabled ? (
          <Message>Database management is disabled on this server. Enable management and configure allowed source roots to build databases.</Message>
        ) : view === "databases" ? (
          <DatabaseManager
            token={token}
            config={config.data}
            repositories={repositories.data?.items ?? []}
            databases={databases.data?.items ?? []}
            jobs={jobs.data?.items ?? []}
            onRepositoryCreated={repositories.refresh}
            onDatabaseCreated={databases.refresh}
            onJobCreated={(job) => {
              setSelectedJobId(job.job_id);
              jobs.refresh();
              setNotice("Build submitted. Follow its status below; published versions remain queryable.");
              onNavigate("build-jobs");
            }}
          />
        ) : (
          <div className="management-job-grid">
            <JobList jobs={jobs.data?.items ?? []} selectedId={selectedJobId} onSelect={setSelectedJobId} onCreate={() => onNavigate("databases")} />
            {selectedJobId ? <JobDetail key={selectedJobId} token={token} jobId={selectedJobId} onChanged={jobs.refresh} /> : <div className="management-empty panel">Select a build to inspect its versions and progress.</div>}
          </div>
        )}
      </>}
    </main>
  );
}

function AdminConnect({ onConnect }: { onConnect: (token: string) => void }) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim() || pending) return;
    setPending(true);
    setError("");
    try {
      await adminRequest<ManagementConfig>("config", draft.trim());
      if (mounted.current) {
        onConnect(draft.trim());
        setDraft("");
      }
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason));
    } finally {
      if (mounted.current) setPending(false);
    }
  };

  return (
    <section className="panel management-connect">
      <span className="management-connect-symbol"><Icon name="database" size={26} /></span>
      <h2>Connect as administrator</h2>
      <p>Enter the server administrator token to manage repositories and builds. Your token stays in this page’s memory and is cleared when you reload.</p>
      <form onSubmit={(event) => void connect(event)} className="management-form">
        <label>Administrator token<input type="password" autoComplete="off" value={draft} onChange={(event) => setDraft(event.target.value)} required /></label>
        {error ? <Message kind="error">{error}</Message> : null}
        <button className="management-button primary" type="submit" disabled={pending || !draft.trim()}>{pending ? "Connecting…" : "Connect"}</button>
      </form>
    </section>
  );
}

interface DatabaseManagerProps {
  token: string;
  config: ManagementConfig;
  repositories: Repository[];
  databases: Database[];
  jobs: BuildJob[];
  onRepositoryCreated: () => void;
  onDatabaseCreated: () => void;
  onJobCreated: (job: BuildJob) => void;
}

function DatabaseManager({ token, config, repositories, databases, jobs, onRepositoryCreated, onDatabaseCreated, onJobCreated }: DatabaseManagerProps) {
  const [databaseId, setDatabaseId] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [name, setName] = useState("");
  const [profileId, setProfileId] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [selectedTags, setSelectedTags] = useState<Map<string, SourceTag>>(() => new Map());
  const [tagSearch, setTagSearch] = useState("");
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState("");
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [legacyGeneration, setLegacyGeneration] = useState("");
  const [plan, setPlan] = useState<BuildPlan | null>(null);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const idempotencyKey = useRef("");
  const selectedDatabase = databases.find((item) => item.database_id === (databaseId || databases[0]?.database_id));
  const effectiveRepoId = repositoryId || selectedDatabase?.repo_id || repositories[0]?.repo_id || "";
  const effectiveProfileId = profileId || config.profiles[0]?.profile_id || "";
  const tagParams = new URLSearchParams({ limit: String(TAG_PAGE_SIZE), cursor: cursor || "0", q: query });
  const tags = useResource<TagListing>(effectiveRepoId ? `repositories/${encodeURIComponent(effectiveRepoId)}/tags?${tagParams}` : null, token);
  const space = useResource<SpaceUsage>(selectedDatabase ? `databases/${encodeURIComponent(selectedDatabase.database_id)}/space` : null, token, HISTORY_POLL_MS);
  const publishedTargets = new Set(jobs.flatMap((job) => job.items.filter((item) => item.status === "succeeded").map((item) => item.target_id)));

  useEffect(() => {
    const timer = setTimeout(() => { setQuery(tagSearch.trim()); setCursor(""); setCursorHistory([]); }, 250);
    return () => clearTimeout(timer);
  }, [tagSearch]);

  useEffect(() => {
    setSelectedTags(new Map());
    setPlan(null);
    setCursor("");
    setCursorHistory([]);
  }, [effectiveRepoId, selectedDatabase?.database_id]);

  const invalidatePlan = () => {
    setPlan(null);
    idempotencyKey.current = "";
  };

  const toggleTag = (tag: SourceTag) => {
    invalidatePlan();
    setSelectedTags((previous) => {
      const next = new Map(previous);
      if (next.has(tag.tag)) next.delete(tag.tag);
      else if (next.size < config.max_selected_tags) next.set(tag.tag, tag);
      return next;
    });
  };

  const createDatabase = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !effectiveProfileId || !effectiveRepoId || pending) return;
    setPending("database");
    setError("");
    try {
      const database = await adminRequest<Database>("databases", token, {
        method: "POST", body: { name: name.trim(), repo_id: effectiveRepoId, profile_id: effectiveProfileId },
      });
      onDatabaseCreated();
      setDatabaseId(database.database_id);
      setName("");
      setShowCreate(false);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setPending(""); }
  };

  const prepare = async () => {
    if (!selectedDatabase || !effectiveRepoId || !selectedTags.size || pending) return;
    setPending("plan");
    setError("");
    try {
      const next = await adminRequest<BuildPlan>("build-plans", token, {
        method: "POST",
        body: { database_id: selectedDatabase.database_id, repo_id: effectiveRepoId, tags: [...selectedTags.keys()], ...(legacyGeneration ? { legacy_generation: legacyGeneration } : {}) },
      });
      setPlan(next);
      idempotencyKey.current = typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, "0")).join("");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setPending(""); }
  };

  const start = async () => {
    if (!plan || pending) return;
    setPending("build");
    setError("");
    try {
      const job = await adminRequest<BuildJob>("jobs", token, {
        method: "POST", body: { plan_id: plan.plan_id, idempotency_key: idempotencyKey.current },
      });
      onJobCreated(job);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setPending(""); }
  };

  return (
    <div className="management-databases">
      {error ? <Message kind="error">{error}</Message> : null}
      <div className="management-setup-grid">
        <section className="panel management-section">
          <SectionHeading title="1. Source repository" description="Browse directories on the server. Git objects are read without changing the working tree." />
          <div className="management-form">
            <label>Registered repository<select value={effectiveRepoId} onChange={(event) => { setRepositoryId(event.target.value); invalidatePlan(); }}>
              <option value="" disabled>Select a repository</option>
              {repositories.map((repo) => <option key={repo.repo_id} value={repo.repo_id}>{repo.name} · {repo.path}</option>)}
            </select></label>
            <RepositoryBrowser token={token} config={config} onRegistered={(repository) => { onRepositoryCreated(); setRepositoryId(repository.repo_id); }} />
          </div>
        </section>
        <section className="panel management-section">
          <SectionHeading title="2. Shared database" description="Source content, parsed facts and compatible vectors are reused between versions." action={databases.length ? <button type="button" className="management-button quiet" disabled={Boolean(pending)} onClick={() => setShowCreate(!showCreate)}>{showCreate ? "Close" : "New database"}</button> : undefined} />
          <div className="management-form">
            {databases.length ? <label>Database<select value={selectedDatabase?.database_id ?? ""} disabled={Boolean(pending)} onChange={(event) => { setDatabaseId(event.target.value); setRepositoryId(""); invalidatePlan(); }}>
              {databases.map((database) => <option key={database.database_id} value={database.database_id}>{database.name}</option>)}
            </select></label> : null}
            {!databases.length || showCreate ? <form className="management-form" onSubmit={(event) => void createDatabase(event)}>
              <label>Database name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Unreal Engine" required maxLength={120} /></label>
              <label>Build profile<select value={effectiveProfileId} onChange={(event) => setProfileId(event.target.value)} required>
                <option value="" disabled>Select a configured profile</option>
                {config.profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.label} · {profile.adapter} · {profile.embedding_model}</option>)}
              </select></label>
              {!config.profiles.length ? <p className="management-hint">Add an adapter and embedding profile in the server configuration before creating a database.</p> : null}
              <button type="submit" className="management-button primary" disabled={!effectiveRepoId || !effectiveProfileId || !name.trim() || Boolean(pending)}>{pending === "database" ? "Creating…" : "Create database"}</button>
            </form> : <>
              <p className="management-hint">Profile: <strong>{config.profiles.find((profile) => profile.profile_id === selectedDatabase?.profile_id)?.label ?? selectedDatabase?.profile_id}</strong></p>
              {space.data ? <SpaceBreakdown usage={space.data} /> : space.error ? <p className="management-hint">Storage measurement unavailable: {space.error}</p> : null}
            </>}
          </div>
        </section>
      </div>
      <section className="panel management-section">
        <SectionHeading title="3. Select versions" description="Each selected tag is pinned to its commit during preflight. Selection is kept across pages." action={<span className="management-count">{selectedTags.size} / {config.max_selected_tags} selected</span>} />
        <div className="management-tags-toolbar">
          <label className="management-search"><Icon name="search" size={16} /><span className="sr-only">Search tags</span><input placeholder="Search Git tags…" value={tagSearch} onChange={(event) => setTagSearch(event.target.value)} disabled={!effectiveRepoId} /></label>
          <span className="management-hint">{tags.data ? `${tags.data.total.toLocaleString()} matching tags` : effectiveRepoId ? "Loading tags…" : "Select a repository first"}</span>
          <button className="management-button quiet" type="button" disabled={!selectedTags.size || Boolean(pending)} onClick={() => { setSelectedTags(new Map()); invalidatePlan(); }}>Clear selection</button>
        </div>
        {tags.error ? <Message kind="error">{tags.error}<button type="button" onClick={tags.refresh}>Retry</button></Message> : null}
        <div className="management-table-scroll">
          <table className="management-table tag-table">
            <thead><tr><th><span className="sr-only">Select</span></th><th>Git tag</th><th>UE version</th><th>Pinned commit</th><th>Tag type</th></tr></thead>
            <tbody>{tags.data?.items.map((tag) => <tr key={tag.tag} className={selectedTags.has(tag.tag) ? "selected" : ""}>
              <td><input type="checkbox" aria-label={`Select ${tag.tag}`} checked={selectedTags.has(tag.tag)} onChange={() => toggleTag(tag)} disabled={Boolean(pending) || (!selectedTags.has(tag.tag) && selectedTags.size >= config.max_selected_tags)} /></td>
              <td><span className="management-tag-name">{tag.tag}</span></td>
              <td>{tag.engine_version || "Not detected"}</td><td><code title={tag.commit}>{tag.commit.slice(0, 12)}</code></td><td><span className="management-pill">{tag.annotated ? "Annotated" : "Lightweight"}</span></td>
            </tr>)}</tbody>
          </table>
          {!tags.data?.items.length ? <div className="management-empty">{tags.pending ? "Reading repository tags…" : effectiveRepoId ? "No matching commit tags found." : "Register or select a server repository to browse its tags."}</div> : null}
        </div>
        <div className="management-pagination">
          <button className="management-button secondary" type="button" disabled={!cursorHistory.length || tags.pending} onClick={() => { setCursor(cursorHistory[cursorHistory.length - 1]); setCursorHistory((previous) => previous.slice(0, -1)); }}>Previous</button>
          <span>Page {cursorHistory.length + 1}</span>
          <button className="management-button secondary" type="button" disabled={!tags.data?.next_cursor || tags.pending} onClick={() => { setCursorHistory((previous) => [...previous, cursor]); setCursor(tags.data?.next_cursor ?? ""); }}>Next</button>
        </div>
        {selectedTags.size ? <div className="management-selection">{[...selectedTags.values()].map((tag) => <button type="button" key={tag.tag} onClick={() => toggleTag(tag)} disabled={Boolean(pending)} title={`Remove ${tag.tag}`}>{tag.tag}<Icon name="close" size={12} /></button>)}</div> : null}
        <div className="management-preflight">
          <label>Reuse a legacy generation<select aria-label="Reuse a legacy generation" value={legacyGeneration} onChange={(event) => { setLegacyGeneration(event.target.value); invalidatePlan(); }} disabled={Boolean(pending)}>
            <option value="">Do not import a legacy generation</option>
            {config.legacy_generations?.map((generation) => <option key={`${generation.corpus_id}/${generation.generation_id}`} value={generation.generation_id}>{generation.corpus_id} · {generation.generation_id} · {generation.embedding_model ?? "Check compatibility"}</option>)}
          </select><small>Only vectors with matching source, input and model provenance can be reused.</small></label>
          <button className="management-button primary" type="button" disabled={!selectedDatabase || !effectiveRepoId || !selectedTags.size || Boolean(pending)} onClick={() => void prepare()}>{pending === "plan" ? "Checking selected versions…" : "Preflight selected tags"}<Icon name="chevron" size={15} /></button>
        </div>
      </section>
      {plan ? <section className="panel management-section management-plan">
        <SectionHeading title="4. Review and build" description="The following commits are fixed for this build. Each successful version is published independently." />
        {plan.summary ? <div className="management-plan-summary"><dl className="management-statistics"><div><dt>Selected file occurrences</dt><dd>{plan.summary.file_occurrences.toLocaleString()}</dd></div><div><dt>Logical source size</dt><dd>{bytes(plan.summary.logical_source_bytes)}</dd></div><div><dt>Unique selected content</dt><dd>{bytes(plan.summary.unique_source_bytes)}</dd></div><div><dt>Repeated file occurrences</dt><dd>{plan.summary.shared_file_occurrences.toLocaleString()}</dd></div></dl>{plan.summary.notes.map((note, index) => <p key={index} className="management-hint">{note}</p>)}</div> : null}
        {plan.warnings?.map((warning, index) => <Message key={index}>{warning}</Message>)}
        <div className="management-table-scroll"><table className="management-table"><thead><tr><th>Tag</th><th>Fixed commit</th><th>Query target</th></tr></thead><tbody>{plan.items.map((item) => <tr key={`${item.tag}/${item.commit}`}><td>{item.tag}</td><td><code title={item.commit}>{item.commit.slice(0, 12)}</code></td><td><code>{item.target_id}</code>{publishedTargets.has(item.target_id) ? <span className="management-pill succeeded">Published</span> : null}</td></tr>)}</tbody></table></div>
        <div className="management-plan-footer"><p>Compatible content and vectors are reused. Missing vectors are generated using this database’s configured embedding service.</p><button className="management-button primary" type="button" disabled={Boolean(pending)} onClick={() => void start()}><Icon name="play" size={15} />{pending === "build" ? "Submitting…" : `Start build · ${plan.items.length} ${plan.items.length === 1 ? "version" : "versions"}`}</button></div>
      </section> : null}
    </div>
  );
}

function RepositoryBrowser({ token, config, onRegistered }: { token: string; config: ManagementConfig; onRegistered: (repository: Repository) => void }) {
  const [open, setOpen] = useState(false);
  const [rootId, setRootId] = useState("");
  const [path, setPath] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const effectiveRootId = rootId || config.source_roots[0]?.root_id || "";
  const selectedRoot = config.source_roots.find((root) => root.root_id === effectiveRootId);
  const params = new URLSearchParams({ root_id: effectiveRootId, path });
  const listing = useResource<DirectoryListing>(open && effectiveRootId ? `directories?${params}` : null, token);

  const register = async (selectedPath: string) => {
    if (pending) return;
    setPending(true);
    setError("");
    try {
      const repository = await adminRequest<Repository>("repositories", token, { method: "POST", body: { root_id: effectiveRootId, path: selectedPath } });
      onRegistered(repository);
      setOpen(false);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setPending(false); }
  };

  return <div className="management-browser">
    <button type="button" className="management-button secondary" onClick={() => setOpen(!open)}><Icon name="search" size={15} />{open ? "Close server browser" : "Browse server directories"}</button>
    {open ? <div className="management-browser-content">
      {!config.source_roots.length ? <p className="management-hint">No source roots are configured. Add allowed server directories in the management configuration.</p> : <>
        <label>Allowed source root<select value={effectiveRootId} onChange={(event) => { setRootId(event.target.value); setPath(""); }}>
          {config.source_roots.map((root) => <option key={root.root_id} value={root.root_id}>{root.label || root.path}</option>)}
        </select></label>
        <div className="management-directory-path"><code>{selectedRoot?.path}{path ? `/${path}` : ""}</code><button type="button" className="management-button quiet" disabled={!path} onClick={() => setPath(path.split("/").slice(0, -1).join("/"))}>Up</button></div>
        {listing.error ? <Message kind="error">{listing.error}</Message> : null}
        <ul className="management-directory-list">{listing.data?.items.map((directory) => <li key={directory.path}><button type="button" onClick={() => setPath(directory.path)} disabled={pending}><Icon name={directory.is_repository ? "database" : "chevron"} size={15} />{directory.name}</button>{directory.is_repository ? <button className="management-button quiet" type="button" disabled={pending} onClick={() => void register(directory.path)}>Use repository</button> : null}</li>)}</ul>
        {listing.pending ? <span className="management-hint">Reading directories…</span> : !listing.data?.items.length ? <span className="management-hint">No child directories.</span> : null}
        <button className="management-button secondary" type="button" disabled={pending} onClick={() => void register(path)}>{pending ? "Registering…" : "Use current directory as repository"}</button>
      </>}
      {error ? <Message kind="error">{error}</Message> : null}
    </div> : null}
  </div>;
}

function SpaceBreakdown({ usage }: { usage: SpaceUsage }) {
  const breakdown = usage.breakdown ?? usage;
  const measurements = [
    ["Source objects", breakdown.source_bytes ?? usage.physical_source_bytes],
    ["Structure facts", breakdown.structure_bytes],
    ["Vectors and chunks", breakdown.vector_bytes],
    ["Search index history", breakdown.index_bytes],
    ["Version mappings", breakdown.mapping_bytes],
  ] as const;
  return <div className="management-space"><dl>{measurements.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{bytes(value)}</dd></div>)}</dl>
    {typeof breakdown.total_bytes === "number" ? <p>Shared pool physical total: {bytes(breakdown.total_bytes)}</p> : null}
    {typeof usage.vector_count === "number" ? <p>{usage.vector_count.toLocaleString()} unique vectors</p> : null}
    {typeof usage.logical_source_bytes === "number" ? <p>Logical source across versions: {bytes(usage.logical_source_bytes)}</p> : null}
    {usage.notes?.map((note, index) => <p key={index} className="management-hint">{note}</p>)}
  </div>;
}

function JobList({ jobs, selectedId, onSelect, onCreate }: { jobs: BuildJob[]; selectedId: string; onSelect: (id: string) => void; onCreate: () => void }) {
  return <section className="panel management-section management-job-list"><SectionHeading title="Build history" description="State is stored on the server and survives page reloads." />
    {jobs.length ? <ul>{jobs.map((job) => <li key={job.job_id}><button type="button" className={selectedId === job.job_id ? "active" : ""} onClick={() => onSelect(job.job_id)}><span className="management-job-list-line"><strong>{job.total_tags} {job.total_tags === 1 ? "version" : "versions"}</strong><StatusPill status={job.status} /></span><code>{job.job_id}</code><span>{job.completed_tags} / {job.total_tags} published · {dateTime(job.created_at)}</span></button></li>)}</ul> : <div className="management-empty"><Icon name="activity" size={25} /><p>No build tasks yet.</p><button type="button" className="management-button secondary" onClick={onCreate}>Select source versions</button></div>}
  </section>;
}

function jobPollingInterval(job: BuildJob | null): number {
  return !job || ACTIVE_STATUSES.has(job.status) ? ACTIVE_POLL_MS : HISTORY_POLL_MS;
}

function JobDetail({ token, jobId, onChanged }: { token: string; jobId: string; onChanged: () => void }) {
  const resource = useResource<BuildJob>(`jobs/${encodeURIComponent(jobId)}`, token, jobPollingInterval);
  const job = resource.data;
  const events = useJobEvents(jobId, token, jobPollingInterval(job));
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const action = async (operation: "cancel" | "resume" | "retry") => {
    if (pending) return;
    setPending(operation);
    setError("");
    try {
      await adminRequest<BuildJob>(`jobs/${encodeURIComponent(jobId)}/${operation}`, token, { method: "POST" });
      resource.refresh();
      onChanged();
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setPending(""); }
  };

  if (!job) return <section className="panel management-section"><SectionHeading title="Build details" description={jobId} />{resource.error ? <Message kind="error">{resource.error}<button type="button" onClick={resource.refresh}>Retry</button></Message> : <div className="management-empty">Loading build…</div>}</section>;
  return <section className="panel management-section management-job-detail">
    <SectionHeading title="Build details" description={job.job_id} action={<StatusPill status={job.status} />} />
    {error || resource.error || job.error ? <Message kind="error">{error || resource.error || jobError(job.error)}</Message> : null}
    <div className="management-job-summary"><div><span>Published versions</span><strong>{job.completed_tags}<small> / {job.total_tags}</small></strong></div><div><span>Current phase</span><strong className="phase-label">{readable(job.phase || job.status)}</strong></div><div><span>Last update</span><strong className="phase-label">{dateTime(job.updated_at)}</strong></div></div>
    <div className="management-batch-progress"><progress max={Math.max(1, job.total_tags)} value={job.completed_tags} aria-label="Published versions" /><span>{job.completed_tags} of {job.total_tags} versions published</span></div>
    <div className="management-actions job-actions">
      {ACTIVE_STATUSES.has(job.status) && job.status !== "cancel_requested" && job.status !== "publishing" ? <button type="button" className="management-button danger" disabled={Boolean(pending)} onClick={() => void action("cancel")}>{pending === "cancel" ? "Requesting cancellation…" : "Cancel remaining work"}</button> : null}
      {RESUMABLE_STATUSES.has(job.status) ? <button type="button" className="management-button primary" disabled={Boolean(pending)} onClick={() => void action("resume")}>{pending === "resume" ? "Resuming…" : "Continue unfinished versions"}</button> : null}
      {RETRYABLE_STATUSES.has(job.status) ? <button type="button" className="management-button primary" disabled={Boolean(pending)} onClick={() => void action("retry")}>{pending === "retry" ? "Retrying…" : "Retry failed versions"}</button> : null}
      {job.status === "cancel_requested" ? <p className="management-hint">Cancellation requested. Waiting for the current operation to finish safely.</p> : null}
      {job.status === "publishing" ? <p className="management-hint">Publishing the verified snapshot. This short commit step will finish before cancellation.</p> : null}
      {job.status === "interrupted" ? <p className="management-hint">The worker stopped before completion. Continue manually to reuse completed objects and vectors.</p> : null}
    </div>
    <div className="management-version-jobs">{job.items.map((item) => {
      const percent = phasePercent(item.completed_units, item.total_units, item.status === "succeeded");
      const elapsed = phaseElapsed(item.phase_started_at, item.ended_at || job.updated_at);
      return <article key={`${item.tag}/${item.commit}`} className="management-version-job"><div className="management-version-heading"><div><strong>{item.tag}</strong><code>{item.commit.slice(0, 12)}</code></div><StatusPill status={item.status} /></div><div className="management-phase"><span>{readable(item.phase || item.status)}</span><span>{item.total_units ? `${item.completed_units.toLocaleString()} / ${item.total_units.toLocaleString()} ${item.unit}` : `${item.completed_units.toLocaleString()} ${item.unit || "units"} processed`}</span></div>
        {percent !== null ? <progress max={100} value={percent} aria-label={`${item.tag} phase progress`} /> : <div className="management-stage-marker"><span />{item.status === "queued" ? "Waiting for worker" : "No measurable total for this stage"}{elapsed ? ` · Elapsed ${elapsed}` : ""}</div>}
        <p className="management-hint">Target: <code>{item.target_id}</code>{item.snapshot_id ? <> · Snapshot: <code>{item.snapshot_id}</code></> : null}</p>
        {item.statistics || item.counts || item.reused_vectors !== undefined ? <Statistics values={{ ...item.counts, ...item.statistics, ...(item.reused_vectors === undefined ? {} : { reused_vectors: item.reused_vectors }), ...(item.new_vectors === undefined ? {} : { new_vectors: item.new_vectors }) }} /> : null}
        {item.error ? <p className="management-inline-error">{jobError(item.error)}</p> : null}
      </article>;
    })}</div>
    {job.statistics ? <Statistics values={job.statistics} /> : null}
    <div className="management-event-heading"><h3>Build events</h3><span>Latest 500 events · refreshed {ACTIVE_STATUSES.has(job.status) ? "every 2 seconds" : "every 5 seconds"}</span></div>
    {events.error ? <Message kind="error">Event refresh failed: {events.error}</Message> : null}
    <ol className="management-events">{events.events.length ? events.events.map((event) => <li key={event.seq}><time>{dateTime(event.at)}</time><strong>{readable(event.type)}</strong><span>{eventDescription(event.payload)}</span></li>) : <li className="management-hint">No events received yet.</li>}</ol>
  </section>;
}

function Statistics({ values }: { values: Record<string, number> }) {
  return <dl className="management-statistics">{Object.entries(values).filter(([, value]) => typeof value === "number").map(([name, value]) => <div key={name}><dt>{readable(name)}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl>;
}

function StatusPill({ status }: { status: string }) {
  return <span className={`management-pill ${status}`}>{readable(status)}</span>;
}

function SectionHeading({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <header className="panel-header management-panel-header"><div><h2>{title}</h2><p>{description}</p></div>{action}</header>;
}

function Message({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "error" }) {
  return <div className={`management-message ${kind}`} role={kind === "error" ? "alert" : "status"}><Icon name={kind === "error" ? "alert" : "check"} size={16} /><div>{children}</div></div>;
}

function dateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function eventDescription(payload: Record<string, unknown>): string {
  if (typeof payload.message === "string") return payload.message;
  if (typeof payload.status === "string") {
    const parts = [readable(payload.status)];
    if (typeof payload.phase === "string" && payload.phase !== payload.status) parts.push(readable(payload.phase));
    if (typeof payload.completed_tags === "number" && typeof payload.total_tags === "number") parts.push(`${payload.completed_tags} / ${payload.total_tags} versions published`);
    if (Array.isArray(payload.items)) {
      const current = payload.items.find((item: Record<string, unknown>) => item.status === "running");
      if (current) parts.push(`${String(current.tag)} · ${String(current.completed_units ?? 0)}${current.total_units ? ` / ${String(current.total_units)}` : ""} ${String(current.unit ?? "units")}`);
    }
    if (payload.error) parts.push(jobError(payload.error));
    return parts.join(" · ");
  }
  return Object.entries(payload).map(([name, value]) => `${readable(name)}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`).join(" · ");
}
