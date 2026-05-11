const fs = require('fs');
const crypto = require('crypto');

const PASSWORD_HASH = '5994471abb01112afcc18159f6cc74b4f511b99806da59b3caf5a9c173cacfc5';
const AUTH_KEY = 'pd_auth_token';

function wrapWithPasswordGate(html) {
  // Find where </head> is to inject before it
  const headEnd = html.indexOf('</head>');
  const bodyStart = html.indexOf('<body>');
  const bodyEnd = html.indexOf('</body>');
  
  // Extract parts
  const beforeHead = html.substring(0, headEnd);
  const afterHead = html.substring(headEnd);
  const bodyContent = html.substring(bodyStart + 6, bodyEnd);
  
  // Build the password gate script
  const gateScript = `
<script>
(function() {
  var AUTH_KEY = '${AUTH_KEY}';
  var HASH = '${PASSWORD_HASH}';
  
  function checkAuth() {
    return localStorage.getItem(AUTH_KEY) === HASH;
  }
  
  async function hashPassword(pw) {
    var enc = new TextEncoder().encode(pw);
    var buf = await crypto.subtle.digest('SHA-256', enc);
    return Array.from(new Uint8Array(buf)).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
  }
  
  if (checkAuth()) {
    document.addEventListener('DOMContentLoaded', function() {
      document.getElementById('pd-gate').style.display = 'none';
      document.getElementById('pd-content').style.display = 'block';
    });
  }
  
  window.pdUnlock = async function() {
    var pw = document.getElementById('pd-pw').value;
    var h = await hashPassword(pw);
    if (h === HASH) {
      localStorage.setItem(AUTH_KEY, HASH);
      document.getElementById('pd-gate').style.display = 'none';
      document.getElementById('pd-content').style.display = 'block';
    } else {
      document.getElementById('pd-error').style.display = 'block';
      document.getElementById('pd-pw').value = '';
      document.getElementById('pd-pw').focus();
    }
  };
})();
</script>
<style>
#pd-gate {
  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  background: linear-gradient(135deg, #1B2A4A 0%, #2d3d6e 100%);
  display: flex; align-items: center; justify-content: center;
  z-index: 99999; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
#pd-gate .gate-box {
  background: #fff; border-radius: 16px; padding: 40px 36px; text-align: center;
  box-shadow: 0 8px 32px rgba(0,0,0,0.2); max-width: 360px; width: 90%;
}
#pd-gate .gate-logo {
  width: 56px; height: 56px; border-radius: 12px; background: #1B2A4A;
  color: #D4A853; font-weight: 800; font-size: 22px; display: flex;
  align-items: center; justify-content: center; margin: 0 auto 16px;
}
#pd-gate h2 { font-size: 20px; color: #1B2A4A; margin-bottom: 4px; }
#pd-gate p { font-size: 13px; color: #888; margin-bottom: 20px; }
#pd-gate input {
  width: 100%; padding: 10px 14px; border: 2px solid #eee; border-radius: 8px;
  font-size: 14px; margin-bottom: 12px; outline: none; box-sizing: border-box;
}
#pd-gate input:focus { border-color: #D4A853; }
#pd-gate button {
  width: 100%; padding: 10px; background: #1B2A4A; color: #fff; border: none;
  border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer;
}
#pd-gate button:hover { background: #2d3d6e; }
#pd-error { display: none; color: #E74C3C; font-size: 12px; margin-top: 8px; }
#pd-content { display: none; }
</style>
`;

  // Inject gate HTML after <body>
  const gateHTML = `
<div id="pd-gate">
  <div class="gate-box">
    <div class="gate-logo">PD</div>
    <h2>Pour Decisions</h2>
    <p>Enter the password to view this report</p>
    <input type="password" id="pd-pw" placeholder="Password" onkeydown="if(event.key==='Enter')pdUnlock()">
    <button onclick="pdUnlock()">Unlock</button>
    <div id="pd-error">Incorrect password. Please try again.</div>
  </div>
</div>
<div id="pd-content">
`;

  // Reconstruct HTML
  const wrappedBody = gateHTML + bodyContent + '\n</div>';
  const result = beforeHead + gateScript + '\n</head>\n<body>\n' + wrappedBody + '\n</body>\n</html>';
  return result;
}

// Process both files. CLI: node wrap_and_deploy.js <srcDir> <outDir>
const srcDir = process.argv[2] || process.env.WRAP_SRC_DIR || '/home/user/workspace/pour-decisions-report';
const outDir = process.argv[3] || process.env.WRAP_OUT_DIR || '/home/user/workspace/pour-decisions-vercel';

if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

for (const file of ['index.html', 'reviews.html', 'deliveries.html']) {
  const srcPath = `${srcDir}/${file}`;
  if (!fs.existsSync(srcPath)) {
    console.error(`Missing source: ${srcPath}`);
    process.exit(1);
  }
  const src = fs.readFileSync(srcPath, 'utf8');
  // Idempotency guard: if file is already wrapped (contains our localStorage key + checkAuth), skip.
  if (src.includes("localStorage.getItem(AUTH_KEY)") && src.includes("pd_auth_token")) {
    console.log(`${file}: already wrapped (skipping)`);
    if (srcDir !== outDir) fs.writeFileSync(`${outDir}/${file}`, src);
    continue;
  }
  const wrapped = wrapWithPasswordGate(src);
  fs.writeFileSync(`${outDir}/${file}`, wrapped);
  console.log(`${file}: ${src.length} -> ${wrapped.length} bytes (password-protected)`);
}

// Copy pnl.html unchanged if present in src
const pnlSrc = `${srcDir}/pnl.html`;
if (fs.existsSync(pnlSrc)) {
  fs.copyFileSync(pnlSrc, `${outDir}/pnl.html`);
  console.log(`pnl.html: copied unchanged`);
}

console.log('\nDone! Files ready for deployment.');
