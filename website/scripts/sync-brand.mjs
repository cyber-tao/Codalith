import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const source = new URL('../../docs/assets/brand/', import.meta.url);
const destination = new URL('../public/brand/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const name of ['codalith-mark.png', 'codalith-logo.png', 'codalith-logo-dark.png']) {
  await copyFile(fileURLToPath(new URL(name, source)), fileURLToPath(new URL(name, destination)));
}
