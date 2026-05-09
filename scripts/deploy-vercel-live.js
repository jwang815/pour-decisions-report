const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const TOKEN = process.argv[2] || process.env.VERCEL_TOKEN;
const PROJECT_DIR = process.argv[3] || process.env.VERCEL_PROJECT_DIR || '/home/user/workspace/pour-decisions-vercel';
const PROJECT_NAME = process.env.VERCEL_PROJECT_NAME || 'pour-decisions-report';

if (!TOKEN) {
  console.error('Missing VERCEL_TOKEN — pass as argv[2] or env var');
  process.exit(1);
}
if (!fs.existsSync(PROJECT_DIR)) {
  console.error(`Project dir does not exist: ${PROJECT_DIR}`);
  process.exit(1);
}
const API = 'https://api.vercel.com';

async function apiFetch(endpoint, options = {}) {
  const res = await fetch(`${API}${endpoint}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      ...options.headers,
    },
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  return { status: res.status, data };
}

async function uploadFile(filePath) {
  const content = fs.readFileSync(filePath);
  const sha = crypto.createHash('sha1').update(content).digest('hex');
  
  const res = await fetch(`${API}/v2/files`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      'Content-Type': 'application/octet-stream',
      'x-vercel-digest': sha,
      'Content-Length': content.length.toString(),
    },
    body: content,
  });
  
  console.log(`  Upload ${path.basename(filePath)} (${content.length} bytes): ${res.status}`);
  return { sha, size: content.length };
}

async function deploy() {
  console.log('=== Deploying to Vercel ===\n');
  
  // Step 1: Read files
  const files = fs.readdirSync(PROJECT_DIR)
    .filter(f => fs.statSync(path.join(PROJECT_DIR, f)).isFile());
  console.log(`Files: ${files.join(', ')}\n`);
  
  // Step 2: Upload each file
  console.log('Uploading files...');
  const fileSpecs = [];
  for (const file of files) {
    const result = await uploadFile(path.join(PROJECT_DIR, file));
    fileSpecs.push({ file: file, sha: result.sha, size: result.size });
  }
  
  // Step 3: Create deployment
  console.log('\nCreating deployment...');
  const deployRes = await apiFetch('/v13/deployments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: PROJECT_NAME,
      files: fileSpecs,
      target: 'production',
      projectSettings: { framework: null },
    }),
  });
  
  if (deployRes.status === 200 || deployRes.status === 201) {
    const d = deployRes.data;
    console.log('\n=== SUCCESS ===');
    console.log('Deployment ID:', d.id);
    console.log('URL:', `https://${d.url}`);
    console.log('State:', d.readyState || d.status);
    
    if (d.alias && d.alias.length > 0) {
      console.log('Production aliases:');
      d.alias.forEach(a => console.log(`  https://${a}`));
    }
    
    // Step 4: Wait for READY state
    if (d.readyState !== 'READY') {
      console.log('\nWaiting for deployment to be ready...');
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const check = await apiFetch(`/v13/deployments/${d.id}`);
        const state = check.data.readyState || check.data.status;
        process.stdout.write(`  ${state}...`);
        if (state === 'READY') {
          console.log(' Done!');
          console.log('\nProduction URL: https://' + (check.data.alias?.[0] || check.data.url));
          break;
        }
        if (state === 'ERROR' || state === 'CANCELED') {
          console.log('\nDeployment failed:', state);
          console.log(JSON.stringify(check.data.errors || check.data, null, 2));
          break;
        }
      }
    }
    
    // Step 5: Get project info for the production domain
    const projRes = await apiFetch(`/v9/projects/${PROJECT_NAME}`);
    if (projRes.status === 200 && projRes.data.alias) {
      console.log('\nProject domains:');
      projRes.data.alias.forEach(a => console.log(`  https://${a.domain}`));
    }
    
  } else {
    console.error('\nDeployment failed:', deployRes.status);
    console.error(JSON.stringify(deployRes.data, null, 2));
  }
}

deploy().catch(e => {
  console.error('Fatal:', e.message);
  process.exit(1);
});
