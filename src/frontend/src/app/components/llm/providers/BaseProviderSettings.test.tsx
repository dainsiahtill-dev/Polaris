import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BaseProviderSettings } from "./BaseProviderSettings";
import type { ProviderConfig } from "../types";

const validateOk = () => ({
  valid: true,
  errors: [],
  warnings: [],
});

describe("BaseProviderSettings", () => {
  it("updates max_context_tokens from context window input", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "openai_compat",
      name: "OpenAI Compat",
      base_url: "https://api.example.com/v1",
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    fireEvent.change(screen.getByTestId("provider-max-context-tokens-input"), {
      target: { value: "200000" },
    });

    expect(onUpdate).toHaveBeenCalledWith({ max_context_tokens: 200000 });
  });

  it("updates max_output_tokens from output input", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "anthropic_compat",
      name: "Anthropic Compat",
      base_url: "https://api.example.com/v1",
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    fireEvent.change(screen.getByTestId("provider-max-output-tokens-input"), {
      target: { value: "8192" },
    });

    expect(onUpdate).toHaveBeenCalledWith({ max_output_tokens: 8192 });
  });

  it("falls back to max_tokens when max_output_tokens is missing", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "openai_compat",
      name: "OpenAI Compat",
      base_url: "https://api.example.com/v1",
      max_tokens: 2048,
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    const input = screen.getByTestId(
      "provider-max-output-tokens-input",
    ) as HTMLInputElement;
    expect(input.value).toBe("2048");
  });

  it("clears max_output_tokens when input is emptied", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "kimi",
      name: "Kimi",
      base_url: "https://api.moonshot.cn/v1",
      max_output_tokens: 4096,
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    fireEvent.change(screen.getByTestId("provider-max-output-tokens-input"), {
      target: { value: "" },
    });

    expect(onUpdate).toHaveBeenCalledWith({ max_output_tokens: undefined });
  });

  it("updates model capability profile to compact/slim", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "openai_compat",
      name: "Local Compat",
      base_url: "http://127.0.0.1:8000/v1",
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    fireEvent.change(screen.getByTestId("provider-model-capability-select"), {
      target: { value: "compact" },
    });

    expect(onUpdate).toHaveBeenCalledWith({
      execution_profile: "compact",
      tool_schema_profile: "slim",
    });
  });

  it("updates model capability profile to full/full", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "openai_compat",
      name: "Strong Compat",
      base_url: "https://api.example.com/v1",
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    fireEvent.change(screen.getByTestId("provider-model-capability-select"), {
      target: { value: "full" },
    });

    expect(onUpdate).toHaveBeenCalledWith({
      execution_profile: "full",
      tool_schema_profile: "full",
    });
  });

  it("clears model capability profile for automatic evaluation", () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: "ollama",
      name: "Ollama",
      base_url: "http://127.0.0.1:11434",
      execution_profile: "compact",
      tool_schema_profile: "slim",
    };

    render(
      <BaseProviderSettings
        provider={provider}
        onUpdate={onUpdate}
        onValidate={validateOk}
      />,
    );

    const select = screen.getByTestId(
      "provider-model-capability-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("compact");

    fireEvent.change(select, {
      target: { value: "auto" },
    });

    expect(onUpdate).toHaveBeenCalledWith({
      execution_profile: undefined,
      tool_schema_profile: undefined,
    });
  });
});
