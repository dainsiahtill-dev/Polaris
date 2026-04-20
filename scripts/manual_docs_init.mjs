// Use global fetch from Node.js 22+
const BASE_URL = 'http://127.0.0.1:51197';
const TOKEN = '692b8cf60c1f57255594d65ec5183713';
const WORKSPACE = 'C:/Temp/NewDemoProject';

const headers = {
  'Authorization': `Bearer ${TOKEN}`,
  'Content-Type': 'application/json',
};

// Minimal project documentation to apply directly
const projectDocs = {
  mode: 'minimal',
  target_root: 'workspace/docs',
  files: [
    {
      path: 'workspace/docs/SPEC.md',
      content: `# NewDemoProject 规格说明书

## 项目目标
创建一个简单的文件服务器（Node.js + Express）

## 核心功能
- 文件上传接口
- 文件列表展示
- 文件下载接口

## 技术栈
- Node.js 18+
- Express
- 跨平台（Windows/Linux/macOS）

## 验收标准
- API 接口测试通过
- 基本功能可正常运行
`
    },
    {
      path: 'workspace/docs/product/plan.md',
      content: `# NewDemoProject 实施计划

## 阶段 1: 工程初始化
- 初始化 Node.js 项目
- 配置 TypeScript
- 配置 Express 基础结构

## 阶段 2: 核心功能
- 实现文件上传 API
- 实现文件列表 API
- 实现文件下载 API

## 阶段 3: 测试与文档
- 编写单元测试
- 编写 API 集成测试
- 完善 README
`
    },
    {
      path: 'workspace/docs/ROADMAP.md',
      content: `# NewDemoProject 路线图

## 阶段 1: 工程初始化
- 初始化 Node.js 项目
- 配置 TypeScript
- 配置 Express 基础结构

## 阶段 2: 核心功能
- 实现文件上传 API
- 实现文件列表 API
- 实现文件下载 API

## 阶段 3: 测试与文档
- 编写单元测试
- 编写 API 集成测试
- 完善 README
`
    },
    {
      path: 'workspace/docs/ACCEPTANCE.md',
      content: `# NewDemoProject 验收标准

## 功能验收
1. 文件上传接口正常响应
2. 文件列表返回正确数据
3. 文件下载功能可用

## 验收命令
\`\`\`bash
npm install
npm run dev
\`\`\`
`
    }
  ]
};

async function applyDocsDirectly() {
  console.log('=== 手动创建 docs ===');
  
  const res = await fetch(`${BASE_URL}/docs/init/apply`, {
    method: 'POST',
    headers,
    body: JSON.stringify(projectDocs),
  });

  const data = await res.json();
  console.log('响应状态:', res.ok ? 'OK' : 'FAILED');
  console.log('响应:', JSON.stringify(data, null, 2));
  
  return { data, ok: res.ok };
}

async function verifyDocs() {
  console.log('\n=== 验证结果 ===');
  
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
    // Apply docs directly (bypassing LLM)
    const result = await applyDocsDirectly();
    if (!result.ok) {
      console.error('创建 docs 失败');
      process.exit(1);
    }
    
    // Verify
    await verifyDocs();
    
    console.log('\n=== 流程完成 ===');
  } catch (error) {
    console.error('错误:', error.message);
    console.error(error.stack);
    process.exit(1);
  }
}

main();
