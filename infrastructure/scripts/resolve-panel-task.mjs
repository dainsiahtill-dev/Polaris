import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const defaultDictionaryPath = path.join(repoRoot, "infrastructure", "e2e", "panel-locators.json");

function normalizeText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/\s+/g, "")
    .replace(/[，。、“”"'`~!@#$%^&*()_+=\[\]{}|\\:;<>?,./\-]/g, "");
}

function cloneValue(value) {
  return JSON.parse(JSON.stringify(value));
}

function readDictionary(dictionaryPath = defaultDictionaryPath) {
  const raw = fs.readFileSync(dictionaryPath, "utf-8").replace(/^\uFEFF/, "");
  return JSON.parse(raw);
}

function findEntryByAliases(entries, normalizedPrompt) {
  for (const entry of entries || []) {
    for (const alias of entry.aliases || []) {
      if (normalizedPrompt.includes(normalizeText(alias))) {
        return entry;
      }
    }
  }
  return null;
}

function matchesAnyAlias(aliases, normalizedPrompt) {
  return (aliases || []).some((alias) => normalizedPrompt.includes(normalizeText(alias)));
}

function extractPreferredInputValue(prompt, fallbackValue) {
  const jsonMatch = prompt.match(/\{[\s\S]*\}/);
  if (jsonMatch) {
    return jsonMatch[0].trim();
  }
  return fallbackValue;
}

function deriveExpectContains(inputValue, fallbackContains) {
  if (fallbackContains) {
    return fallbackContains;
  }
  try {
    const parsed = JSON.parse(inputValue);
    const keys = Object.keys(parsed);
    if (keys.length > 0) {
      return keys[0];
    }
  } catch {
    // ignore parse failure
  }
  if (typeof inputValue === "string" && inputValue.length > 0) {
    return inputValue.slice(0, Math.min(inputValue.length, 16));
  }
  return "";
}

export function resolvePanelTaskFromPrompt(prompt, options = {}) {
  const trimmedPrompt = String(prompt || "").trim();
  if (!trimmedPrompt) {
    throw new Error("Prompt is required for panel task resolution.");
  }

  const dictionaryPath = options.dictionaryPath || defaultDictionaryPath;
  const requireProviderMatch = options.requireProviderMatch !== false;
  const requireFieldMatch = options.requireFieldMatch !== false;
  const dictionary = readDictionary(dictionaryPath);
  const normalizedPrompt = normalizeText(trimmedPrompt);

  const settingsEntry = dictionary.navigation?.settings || null;
  const llmEntry = dictionary.navigation?.llm_settings || null;
  const providerEntry = findEntryByAliases(dictionary.providers || [], normalizedPrompt);
  const matchedFieldEntry = findEntryByAliases(dictionary.fields || [], normalizedPrompt);
  const fallbackFieldEntry = !requireFieldMatch ? ((dictionary.fields || [])[0] || null) : null;
  const fieldEntry = matchedFieldEntry || fallbackFieldEntry;

  if (!settingsEntry || !llmEntry) {
    throw new Error("Locator dictionary is missing required settings/llm entries.");
  }
  if (!providerEntry && requireProviderMatch) {
    throw new Error(
      `No provider alias matched from dictionary (${dictionaryPath}). ` +
      "Update panel-locators.json or pass --allow-provider-fallback to bypass.",
    );
  }
  if (!fieldEntry) {
    throw new Error(
      `No field alias matched from dictionary (${dictionaryPath}). ` +
      "Update panel-locators.json or pass --allow-field-fallback to bypass.",
    );
  }

  const settingsMentioned = matchesAnyAlias(settingsEntry.aliases, normalizedPrompt);
  const llmMentioned = matchesAnyAlias(llmEntry.aliases, normalizedPrompt);
  const providerMentioned = Boolean(providerEntry);
  const fieldMentioned = Boolean(fieldEntry);

  const navigationSteps = [];
  if (settingsMentioned || llmMentioned || providerMentioned || fieldMentioned) {
    navigationSteps.push(cloneValue(settingsEntry.step));
  }
  if (llmMentioned || providerMentioned || fieldMentioned) {
    navigationSteps.push(cloneValue(llmEntry.step));
  }
  if (providerEntry) {
    navigationSteps.push(cloneValue(providerEntry.step));
  }

  const fieldAction = cloneValue(fieldEntry.action);
  fieldAction.inputValue = extractPreferredInputValue(trimmedPrompt, fieldAction.inputValue);
  fieldAction.expectContains = deriveExpectContains(fieldAction.inputValue, fieldAction.expectContains);

  const inputIntentAliases = dictionary.intents?.input || [];
  const isInputIntent = matchesAnyAlias(inputIntentAliases, normalizedPrompt) || /输入框|input/i.test(trimmedPrompt);

  const warnings = [];
  if (!providerEntry && !requireProviderMatch) {
    warnings.push("No provider alias matched; steps stop at LLM settings page.");
  }
  if (!matchedFieldEntry && !requireFieldMatch) {
    warnings.push("No field alias matched; falling back to first dictionary field.");
  }
  if (!isInputIntent) {
    warnings.push("Input intent not explicitly matched; defaulting to input assertion.");
  }

  return {
    prompt: trimmedPrompt,
    createdAtUtc: new Date().toISOString(),
    dictionaryPath,
    resolved: {
      providerId: providerEntry?.id || null,
      fieldId: fieldEntry.id,
      intent: isInputIntent ? "input" : "input-default",
      requireProviderMatch,
      requireFieldMatch,
      warnings,
    },
    gateConfig: {
      strictErrors: true,
      strictTerminalErrors: true,
      startupSettleMs: 1200,
      postActionSettleMs: 800,
    },
    navigationSteps,
    fieldAction,
  };
}

function main() {
  const args = process.argv.slice(2);
  const allowProviderFallback = args.includes("--allow-provider-fallback");
  const allowFieldFallback = args.includes("--allow-field-fallback");
  const dictionaryIndex = args.indexOf("--dictionary");
  const dictionaryPath =
    dictionaryIndex >= 0 && dictionaryIndex + 1 < args.length
      ? args[dictionaryIndex + 1]
      : undefined;
  const prompt = args
    .filter((arg, index) => {
      if (arg === "--allow-provider-fallback" || arg === "--allow-field-fallback" || arg === "--dictionary") {
        return false;
      }
      if (dictionaryIndex >= 0 && index === dictionaryIndex + 1) {
        return false;
      }
      return true;
    })
    .join(" ")
    .trim();
  if (!prompt) {
    console.error(
      "Usage: node infrastructure/scripts/resolve-panel-task.mjs " +
      "[--dictionary <path>] [--allow-provider-fallback] [--allow-field-fallback] \"<task prompt>\"",
    );
    process.exit(1);
  }
  const resolved = resolvePanelTaskFromPrompt(prompt, {
    dictionaryPath,
    requireProviderMatch: !allowProviderFallback,
    requireFieldMatch: !allowFieldFallback,
  });
  process.stdout.write(`${JSON.stringify(resolved, null, 2)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === currentFile) {
  main();
}
