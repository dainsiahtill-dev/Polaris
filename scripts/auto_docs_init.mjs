// Use global fetch from Node.js 22+
const BASE_URL = 'http://127.0.0.1:52913';
const TOKEN = '932e2058eff7ad7f281da3f8d7c2d9b3';
const WORKSPACE = 'C:/Temp/NewDemoProject';

const headers = {
  'Authorization': `Bearer ${TOKEN}`,
  'Content-Type': 'application/json',
};

async function checkAuth() {
  console.log('=== 步骤 0: 验证认证 ===');
  const res = await fetch(`${BASE_URL}/settings`, { headers });
  const data = await res.json();
  console.log('当前 workspace:', data.workspace);
  console.log('认证状态:', res.ok ? 'OK' : 'FAILED');
  return res.ok;
}

async function runDialogueRound1() {
  console.log('\n=== 步骤 1: 发起第一轮奏对 ===');
  
  const payload = {
    message: '创建一个简单的文件服务器',
    goal: '创建一个简单的文件服务器（Node.js + Express）',
    in_scope: '',
    out_of_scope: '',
    constraints: '',
    definition_of_done: '',
    backlog: '',
    history: [],
  };

  const res = await fetch(`${BASE_URL}/docs/init/dialogue`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  console.log('奏对响应状态:', res.ok ? 'OK' : 'FAILED');
  console.log('回复内容:', data.reply?.substring(0, 200) || 'N/A');
  console.log('问题:', data.questions || []);
  console.log('条陈:', data.tiaochen || []);
  console.log('元数据:', data.meta);
  
  return { data, ok: res.ok };
}

async function runDialogueRound2(history) {
  console.log('\n=== 步骤 2: 回答问题 - 第二轮奏对 ===');
  
  // Answer the questions from round 1
  const answer = '1. Windows/Linux/macOS 跨平台，使用 Node.js 18+\n2. 上传文件 -> 保存到本地 -> 返回文件列表\n3. 无外部依赖，使用 Node.js 内置模块';
  
  const payload = {
    message: answer,
    goal: '创建一个简单的文件服务器（Node.js + Express）',
    in_scope: '',
    out_of_scope: '',
    constraints: '',
    definition_of_done: '',
    backlog: '',
    history: history,
  };

  const res = await fetch(`${BASE_URL}/docs/init/dialogue`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  console.log('奏对响应状态:', res.ok ? 'OK' : 'FAILED');
  console.log('回复内容:', data.reply?.substring(0, 200) || 'N/A');
  console.log('问题:', data.questions || []);
  console.log('条陈:', data.tiaochen || []);
  console.log('元数据:', data.meta);
  
  return { data, ok: res.ok };
}

async function buildPreview(payload) {
  console.log('\n=== 步骤 3: 拟定条陈 ===');
  
  const previewPayload = {
    mode: 'minimal',
    goal: payload.goal || '',
    in_scope: payload.in_scope || '',
    out_of_scope: payload.out_of_scope || '',
    constraints: payload.constraints || '',
    definition_of_done: payload.definition_of_done || '',
    backlog: payload.backlog || '',
  };

  const res = await fetch(`${BASE_URL}/docs/init/preview`, {
    method: 'POST',
    headers,
    body: JSON.stringify(previewPayload),
  });

  const data = await res.json();
  console.log('条陈预览状态:', res.ok ? 'OK' : 'FAILED');
  
  if (res.ok && data.files) {
    console.log('生成文件数量:', data.files.length);
    console.log('目标目录:', data.target_root);
  } else {
    console.log('响应:', JSON.stringify(data).substring(0, 500));
  }
  
  return { data, ok: res.ok };
}

async function applyDocs(previewData) {
  console.log('\n=== 步骤 4: 批红用印 ===');
  
  const applyPayload = {
    mode: previewData.mode || 'minimal',
    target_root: previewData.target_root,
    files: previewData.files.map(f => ({
      path: f.path,
      content: f.content,
    })),
  };

  const res = await fetch(`${BASE_URL}/docs/init/apply`, {
    method: 'POST',
    headers,
    body: JSON.stringify(applyPayload),
  });

  const data = await res.json();
  console.log('用印状态:', res.ok ? 'OK' : 'FAILED');
  console.log('响应:', data);
  
  return { data, ok: res.ok };
}

async function verifyDocs() {
  console.log('\n=== 步骤 5: 验证结果 ===');
  
  const fs = await import('fs');
  
  // Check if docs directory exists
  const docsPath = WORKSPACE + '/docs';
  const docsExists = fs.existsSync(docsPath);
  console.log('docs 目录存在:', docsExists);
  
  if (docsExists) {
    const files = fs.readdirSync(docsPath);
    console.log('docs 文件列表:', files);
    
    // List all files recursively
    for (const file of files) {
      const fullPath = docsPath + '/' + file;
      const stat = fs.statSync(fullPath);
      if (stat.isDirectory()) {
        const subFiles = fs.readdirSync(fullPath);
        console.log(`  ${file}/:`, subFiles);
      }
    }
  }
  
  // Check runtime contracts
  const settingsRes = await fetch(`${BASE_URL}/settings`, { headers });
  const settings = await settingsRes.json();
  console.log('runtime root:', settings.runtime?.root);
  
  return docsExists;
}

async function main() {
  try {
    // Step 0: Verify auth
    await checkAuth();
    
    // Step 1: Run first dialogue round
    const dialogue1Result = await runDialogueRound1();
    if (!dialogue1Result.ok) {
      console.error('第一轮奏对失败，退出');
      process.exit(1);
    }
    
    // Build history for round 2
    const history = [
      { role: 'user', content: '创建一个简单的文件服务器（Node.js + Express）' },
      { role: 'assistant', content: dialogue1Result.data.reply || '', questions: dialogue1Result.data.questions || [] }
    ];
    
    // Step 2: Run second dialogue round to answer questions
    const dialogue2Result = await runDialogueRound2(history);
    if (!dialogue2Result.ok) {
      console.error('第二轮奏对失败，退出');
      process.exit(1);
    }
    
    // Use fields from dialogue response
    const fields = dialogue2Result.data.fields || {};
    const tiaochen = dialogue2Result.data.tiaochen || [];
    
    // Step 3: Build preview with dialogue data
    const previewPayload = {
      goal: fields.goal || '创建一个简单的文件服务器（Node.js + Express）',
      in_scope: fields.in_scope || '',
      out_of_scope: fields.out_of_scope || '',
      constraints: fields.constraints || '',
      definition_of_done: fields.definition_of_done || '',
      backlog: fields.backlog || tiaochen.join('\n'),
    };
    
    const previewResult = await buildPreview(previewPayload);
    if (!previewResult.ok) {
      console.error('拟定条陈失败，退出');
      process.exit(1);
    }
    
    // Step 4: Apply (stamp)
    const applyResult = await applyDocs(previewResult.data);
    if (!applyResult.ok) {
      console.error('批红用印失败，退出');
      process.exit(1);
    }
    
    // Step 5: Verify
    await verifyDocs();
    
    console.log('\n=== 流程完成 ===');
  } catch (error) {
    console.error('错误:', error.message);
    console.error(error.stack);
    process.exit(1);
  }
}

main();
