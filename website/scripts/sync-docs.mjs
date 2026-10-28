import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('../../', import.meta.url));
const output = path.join(repository, 'website/src/content/docs/guides');
const sourceUrl = 'https://github.com/cyber-tao/Codalith/blob/master/';
const pages = [
  ['installation', 'Install the skill and MCP', 'Connect supported coding agents, preview configuration changes, and recover from installation errors.'],
  ['configuration', 'Configuration', 'Configure directory corpora, shared Git databases, management profiles, embedding providers, and source policy.'],
  ['mcp-api', 'MCP API', 'Read-only MCP tools, version catalogs, canonical source citations, resource contracts, and stable error codes.'],
  ['operations', 'Operations', 'Build versioned source databases, manage durable jobs, bind UE projects, and operate local or Docker services.'],
  ['architecture', 'Architecture and invariants', 'Understand shared immutable snapshots, directory generations, structural and semantic retrieval, and source confinement.'],
  ['evaluation', 'Evaluation', 'Run real MCP benchmarks and understand datasets, quality gates, citation checks, and agent comparisons.'],
];
const slugs = new Set(pages.map(([slug]) => slug));

function rewriteLink(destination) {
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/|#|\/)/i.test(destination)) return destination;
  const [file, fragment] = destination.split('#', 2);
  const relative = path.posix.normalize(path.posix.join('docs', file));
  const slug = path.posix.basename(relative, '.md');
  const anchor = fragment === undefined ? '' : `#${fragment}`;
  if (relative === `docs/${slug}.md` && slugs.has(slug)) return `../${slug}/${anchor}`;
  return `${sourceUrl}${relative}${anchor}`;
}

function prepareMarkdown(markdown) {
  let fence;
  return markdown.replace(/^# [^\r\n]+\r?\n\s*/, '').split(/\r?\n/).map((line) => {
    const delimiter = line.match(/^\s*(`{3,}|~{3,})/);
    if (delimiter) {
      if (!fence) fence = delimiter[1][0];
      else if (fence === delimiter[1][0]) fence = undefined;
      return line;
    }
    if (fence) return line;
    return line.replace(/(\[[^\]]*\]\()([^\s)]+)([^)]*\))/g, (_, start, destination, end) => `${start}${rewriteLink(destination)}${end}`);
  }).join('\n');
}

const documents = await Promise.all(pages.map(async ([slug, title, description]) => {
  const markdown = await readFile(path.join(repository, `docs/${slug}.md`), 'utf8');
  const editUrl = `https://github.com/cyber-tao/Codalith/edit/master/docs/${slug}.md`;
  const frontmatter = `---\ntitle: ${JSON.stringify(title)}\ndescription: ${JSON.stringify(description)}\neditUrl: ${JSON.stringify(editUrl)}\n---\n\n`;
  return { slug, content: frontmatter + prepareMarkdown(markdown).trim() + '\n' };
}));
await mkdir(output, { recursive: true });
for (const { slug, content } of documents) {
  await writeFile(path.join(output, `${slug}.md`), content, 'utf8');
}
console.log(`Synced ${documents.length} documentation pages from docs/.`);
