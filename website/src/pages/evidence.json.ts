import evidence from '../evidence/sample.json';

export function GET() {
  return new Response(JSON.stringify(evidence, null, 2), {
    headers: { 'Content-Type': 'application/json' },
  });
}
