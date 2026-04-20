// Analyze PM quality
const BASE_URL = 'http://127.0.0.1:51197';
const TOKEN = '692b8cf60c1f57255594d65ec5183713';

const headers = {
  'Authorization': `Bearer ${TOKEN}`,
  'Content-Type': 'application/json',
};

const LEAKAGE_KEYWORDS = [
  // English
  'you are', 'role:', 'system prompt', 'no yapping', '<system>', 
  'assistant prefix', 'ignore previous', 'disregard instructions',
  // Chinese
  '提示词', '角色设定', '<thinking>', '<tool_call>', '系统提示',
  '你是', '角色扮演', '忽略之前', '无视指令'
];

async function analyzePMQuality() {
  console.log('=== PM 质量分析 ===\n');
  
  // Get snapshot
  const snapshotRes = await fetch(`${BASE_URL}/state/snapshot`, { headers });
  const snapshot = await snapshotRes.json();
  
  const tasks = snapshot.tasks || [];
  const pmState = snapshot.pm_state || {};
  
  console.log('=== 基本信息 ===');
  console.log('任务数量:', tasks.length);
  console.log('已完成任务:', pmState.completed_task_count || 0);
  console.log('最后 Director 状态:', pmState.last_director_status || 'N/A');
  console.log('Run ID:', snapshot.run_id || 'N/A');
  
  // Analyze each task for quality
  let hasGoals = false;
  let hasScope = false;
  let hasSteps = false;
  let hasAcceptance = false;
  let criticalIssues = 0;
  let leakageFound = false;
  let leakageKeywords = [];
  
  for (const task of tasks) {
    console.log(`\n--- 任务: ${task.id} - ${task.title} ---`);
    
    // Check goal
    if (task.goal && task.goal.length > 10) {
      hasGoals = true;
      console.log('✓ 有目标 (goal)');
    } else {
      console.log('✗ 缺少目标 (goal)');
      criticalIssues++;
    }
    
    // Check scope
    if ((task.scope_paths && task.scope_paths.length > 0) || 
        (task.target_files && task.target_files.length > 0)) {
      hasScope = true;
      console.log('✓ 有作用域 (scope)');
    } else {
      console.log('✗ 缺少作用域 (scope)');
      criticalIssues++;
    }
    
    // Check executable steps (chief_engineer.construction_plan)
    if (task.chief_engineer && 
        task.chief_engineer.construction_plan &&
        task.chief_engineer.construction_plan.file_plans &&
        task.chief_engineer.construction_plan.file_plans.length > 0) {
      hasSteps = true;
      console.log('✓ 有可执行步骤 (construction_plan)');
    } else if (task.execution_checklist && task.execution_checklist.length > 0) {
      hasSteps = true;
      console.log('✓ 有执行清单 (execution_checklist)');
    } else {
      console.log('✗ 缺少可执行步骤');
      criticalIssues++;
    }
    
    // Check acceptance criteria
    if ((task.acceptance_criteria && task.acceptance_criteria.length > 0) ||
        (task.acceptance && task.acceptance.length > 0)) {
      hasAcceptance = true;
      console.log('✓ 有验收标准 (acceptance)');
    } else {
      console.log('✗ 缺少验收标准');
      criticalIssues++;
    }
    
    // Check for prompt leakage
    const taskText = JSON.stringify(task).toLowerCase();
    for (const keyword of LEAKAGE_KEYWORDS) {
      if (taskText.includes(keyword.toLowerCase())) {
        leakageFound = true;
        leakageKeywords.push(keyword);
        console.log(`⚠️ 发现关键词泄漏: ${keyword}`);
      }
    }
  }
  
  // Calculate quality score
  let score = 100;
  score -= (tasks.length > 0 && !hasGoals) ? 20 : 0;
  score -= (tasks.length > 0 && !hasScope) ? 15 : 0;
  score -= (tasks.length > 0 && !hasSteps) ? 15 : 0;
  score -= (tasks.length > 0 && !hasAcceptance) ? 20 : 0;
  score -= criticalIssues * 10;
  score -= leakageFound ? 30 : 0;
  score = Math.max(0, score);
  
  // Output quality report
  console.log('\n=== PM 质量评分结果 ===');
  const report = {
    pm_quality_score: score,
    critical_issues: criticalIssues,
    tasks_count: tasks.length,
    completed_tasks: pmState.completed_task_count || 0,
    leakage_found: leakageFound,
    leakage_keywords: [...new Set(leakageKeywords)],
    task_quality: {
      has_goals: hasGoals,
      has_scope: hasScope,
      has_steps: hasSteps,
      has_acceptance: hasAcceptance
    }
  };
  
  console.log(JSON.stringify(report, null, 2));
  
  // Quality gates
  console.log('\n=== 质量门禁检查 ===');
  console.log(`分数 >= 80: ${score >= 80 ? '✓ 通过' : '✗ 失败'} (${score})`);
  console.log(`Critical Issues = 0: ${criticalIssues === 0 ? '✓ 通过' : '✗ 失败'} (${criticalIssues})`);
  console.log(`无提示词泄漏: ${!leakageFound ? '✓ 通过' : '✗ 失败'}`);
  
  return report;
}

analyzePMQuality().catch(console.error);
