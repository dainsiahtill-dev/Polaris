// API Service Layer - Materialization Phase
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.example.com';
const MAX_RETRIES = 3;
const RETRY_DELAY = 1000;

interface RequestConfig extends RequestInit {
  params?: Record<string, string>;
  retry?: boolean;
}

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public statusText: string,
    public data?: any
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchWithRetry(
  url: string,
  config: RequestConfig,
  attempt: number = 1
): Promise<Response> {
  try {
    const response = await fetch(url, config);
    
    if (!response.ok && config.retry && attempt < MAX_RETRIES) {
      throw new ApiError(
        `Request failed with status ${response.status}`,
        response.status,
        response.statusText
      );
    }
    
    return response;
  } catch (error) {
    if (error instanceof ApiError && attempt < MAX_RETRIES) {
      await delay(RETRY_DELAY * attempt);
      return fetchWithRetry(url, config, attempt + 1);
    }
    throw error;
  }
}

function buildUrl(endpoint: string, params?: Record<string, string>): string {
  const url = new URL(endpoint, API_BASE_URL);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.append(key, value);
    });
  }
  return url.toString();
}

export const api = {
  async get<T>(endpoint: string, config: RequestConfig = {}): Promise<T> {
    const url = buildUrl(endpoint, config.params);
    const response = await fetchWithRetry(url, {
      ...config,
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...config.headers,
      },
      retry: config.retry ?? true,
    });
    
    const data = await response.json();
    if (!response.ok) {
      throw new ApiError('GET request failed', response.status, response.statusText, data);
    }
    return data;
  },

  async post<T>(endpoint: string, body: any, config: RequestConfig = {}): Promise<T> {
    const url = buildUrl(endpoint, config.params);
    const response = await fetchWithRetry(url, {
      ...config,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...config.headers,
      },
      body: JSON.stringify(body),
      retry: config.retry ?? false,
    });
    
    const data = await response.json();
    if (!response.ok) {
      throw new ApiError('POST request failed', response.status, response.statusText, data);
    }
    return data;
  },

  async put<T>(endpoint: string, body: any, config: RequestConfig = {}): Promise<T> {
    const url = buildUrl(endpoint, config.params);
    const response = await fetchWithRetry(url, {
      ...config,
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...config.headers,
      },
      body: JSON.stringify(body),
      retry: config.retry ?? false,
    });
    
    const data = await response.json();
    if (!response.ok) {
      throw new ApiError('PUT request failed', response.status, response.statusText, data);
    }
    return data;
  },

  async delete<T>(endpoint: string, config: RequestConfig = {}): Promise<T> {
    const url = buildUrl(endpoint, config.params);
    const response = await fetchWithRetry(url, {
      ...config,
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        ...config.headers,
      },
      retry: config.retry ?? false,
    });
    
    const data = await response.json();
    if (!response.ok) {
      throw new ApiError('DELETE request failed', response.status, response.statusText, data);
    }
    return data;
  },
};

export { ApiError };
export default api;
