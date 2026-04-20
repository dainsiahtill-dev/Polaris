import { useCallback, type ChangeEvent } from 'react';
import { Key } from 'lucide-react';
import { cyberInputClassesAlt } from '@/app/components/ui/cyber-input-classes';

// Cyberpunk style input classes - using alt variant with semi-transparent background
const cyberInputClasses = cyberInputClassesAlt;

interface ProviderInputProps {
  value?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: 'text' | 'password' | 'url' | 'number';
  className?: string;
  disabled?: boolean;
  autoComplete?: string;
  spellCheck?: boolean;
  min?: number;
  max?: number;
  step?: string;
  // 调试标签
  debugLabel?: string;
}

/**
 * 简化的提供商输入组件，作为受控组件交由上层状态管理
 */
export function ProviderInput({
  value,
  onChange,
  placeholder,
  type = 'text',
  className = '',
  disabled = false,
  autoComplete = 'off',
  spellCheck = false,
  min,
  max,
  step,
  debugLabel
}: ProviderInputProps) {
  const handleChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value;
    onChange(newValue);
  }, [onChange]);

  return (
    <input
      type={type}
      value={value ?? ''}
      onChange={handleChange}
      className={`${cyberInputClasses} ${className}`}
      placeholder={placeholder}
      disabled={disabled}
      autoComplete={autoComplete}
      spellCheck={spellCheck}
      min={min}
      max={max}
      step={step}
      data-debug-label={debugLabel}
    />
  );
}

/**
 * 专用的API Key输入组件
 */
interface ApiKeyInputProps {
  apiKey?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  debugLabel?: string;
}

export function ApiKeyInput({ 
  apiKey, 
  onChange, 
  placeholder = "sk-...",
  debugLabel = 'api_key'
}: ApiKeyInputProps) {
  return (
    <div>
      <label className="block text-xs text-text-muted mb-1 flex items-center gap-1">
        <Key className="size-3" />
        API Key
      </label>
      <ProviderInput
        value={apiKey}
        onChange={onChange}
        type="text"
        placeholder={placeholder}
        className="font-mono"
        debugLabel={debugLabel}
      />
      <p className="text-[9px] text-text-dim mt-1">
        API Key用于身份验证，请妥善保管
      </p>
    </div>
  );
}

/**
 * 通用URL输入组件
 */
interface UrlInputProps {
  value?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  description?: string;
  debugLabel?: string;
}

export function UrlInput({ 
  value, 
  onChange, 
  placeholder,
  label = "URL",
  description,
  debugLabel = 'url'
}: UrlInputProps) {
  return (
    <div>
      <label className="block text-xs text-text-muted mb-1">{label}</label>
      <ProviderInput
        value={value}
        onChange={onChange}
        type="url"
        placeholder={placeholder}
        className="font-mono"
        debugLabel={debugLabel}
      />
      {description && (
        <p className="text-[9px] text-text-dim mt-1">{description}</p>
      )}
    </div>
  );
}

/**
 * 通用文本输入组件
 */
interface TextInputProps {
  value?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  description?: string;
  debugLabel?: string;
}

export function TextInput({ 
  value, 
  onChange, 
  placeholder,
  label,
  description,
  debugLabel = 'text'
}: TextInputProps) {
  return (
    <div>
      {label && (
        <label className="block text-xs text-text-muted mb-1">{label}</label>
      )}
      <ProviderInput
        value={value}
        onChange={onChange}
        type="text"
        placeholder={placeholder}
        className="font-mono"
        debugLabel={debugLabel}
      />
      {description && (
        <p className="text-[9px] text-text-dim mt-1">{description}</p>
      )}
    </div>
  );
}

/**
 * 通用数字输入组件
 */
interface NumberInputProps {
  value?: number;
  onChange: (value: number | undefined) => void;
  placeholder?: string;
  label?: string;
  description?: string;
  min?: number;
  max?: number;
  step?: string;
  debugLabel?: string;
}

export function NumberInput({ 
  value, 
  onChange, 
  placeholder,
  label,
  description,
  min,
  max,
  step,
  debugLabel = 'number'
}: NumberInputProps) {
  return (
    <div>
      {label && (
        <label className="block text-xs text-text-muted mb-1">{label}</label>
      )}
      <ProviderInput
        value={value?.toString() || ''}
        onChange={(val) => onChange(val === '' ? undefined : parseFloat(val))}
        type="number"
        placeholder={placeholder}
        min={min}
        max={max}
        step={step}
        debugLabel={debugLabel}
      />
      {description && (
        <p className="text-[9px] text-text-dim mt-1">{description}</p>
      )}
    </div>
  );
}
