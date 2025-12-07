// API service for ASSAI - AI Multi-Modal Platform
// Use /api prefix to leverage Vite proxy and avoid CORS issues
import { ChatRequest, ChatResponse, Conversation, ModelPlugin } from './types';

const API_BASE_URL = "/api";

class AssAI_API {

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    // Development mode - make actual API calls
    const url = `${API_BASE_URL}${endpoint}`;

    // Only add timeout for telemetry requests (500ms)
    // All other routes have no timeout
    const isTelemetry = endpoint === '/telemetry';
    const timeout = isTelemetry ? 1000 : null;

    let controller: AbortController | null = null;
    let timeoutId: NodeJS.Timeout | null = null;

    // Only create timeout controller for telemetry
    if (timeout !== null) {
      controller = new AbortController();
      timeoutId = setTimeout(() => controller!.abort(), timeout);
    }

    // Use provided signal if available, otherwise use our timeout controller (if created)
    const signal = options.signal || (controller ? controller.signal : undefined);

    const config: RequestInit = {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
      ...(signal && { signal }),
    };

    try {
      const response = await fetch(url, config);
      if (timeoutId) {
        clearTimeout(timeoutId);
      }

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
      if (error instanceof Error && error.name === 'AbortError') {
        // Don't throw for aborted requests - they're expected when cancelling
        // Only throw timeout errors if it was our timeout (not user cancellation)
        if (timeout !== null && controller && controller.signal.aborted) {
          console.error(`API request timeout after ${timeout}ms:`, endpoint);
          throw new Error(`Request timeout after ${timeout}ms`);
        }
        // If aborted but not by our timeout, it's a cancellation - rethrow as-is
        throw error;
      }
      console.error('API request failed:', error);
      throw error;
    }
  }

  // Chat endpoints
  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  async getConversations(): Promise<Conversation[]> {
    return this.request<Conversation[]>('/conversations');
  }

  async getConversation(id: string): Promise<Conversation> {
    return this.request<Conversation>(`/conversations/${id}`);
  }

  async deleteConversation(id: string): Promise<void> {
    return this.request<void>(`/conversations/${id}`, {
      method: 'DELETE',
    });
  }

  async createConversation(): Promise<Conversation> {
    return this.request<Conversation>('/conversations', {
      method: 'POST',
    });
  }

  // Model plugins
  async getModels(): Promise<ModelPlugin[]> {
    return this.request<ModelPlugin[]>('/models');
  }

  // Upload endpoints
  async uploadImage(file: File): Promise<{ url: string }> {
    const formData = new FormData();
    formData.append('image', file);

    const response = await fetch(`${API_BASE_URL}/upload/image`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      throw new Error('Image upload failed');
    }

    return response.json();
  }

  async uploadAudio(file: File): Promise<{ url: string }> {
    const formData = new FormData();
    formData.append('audio', file);

    const response = await fetch(`${API_BASE_URL}/upload/audio`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      throw new Error('Audio upload failed');
    }

    return response.json();
  }

  // Text2Image endpoint
  // Backend returns an array of base64 data URIs: ["data:image/png;base64,..."]
  async generateImage(
    prompt: string,
    params?: ImageGenerationParams,
    model?: string,
    sessionId?: string,
    actionId?: number
  ): Promise<string[]> {
    const endpoint = model
      ? `/text2image/model/run/${encodeURIComponent(model)}`
      : '/text2image/model/run';

    const body: any = { prompt, ...params };
    if (sessionId) {
      body.session_id = sessionId;
    }
    if (actionId !== undefined) {
      body.action_id = actionId;
    }

    return this.request<string[]>(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  // Telemetry endpoint
  async getTelemetry(signal?: AbortSignal): Promise<{
    cpu: { memory: [number, number]; load: number };
    gpu: Record<string, { memory: [number, number]; load: number; temp: number; power: number }>;
  }> {
    console.log("HERE telemetry call");
    return this.request('/telemetry', { signal });
  }

  // Hugging Face endpoints
  async getHuggingFaceCache(): Promise<any> {
    return this.request('/huggingface/list');
  }

  async searchHuggingFaceModels(name: string, filter?: string): Promise<any> {
    const endpoint = filter
      ? `/huggingface/search/${encodeURIComponent(name)}/${encodeURIComponent(filter)}`
      : `/huggingface/search/${encodeURIComponent(name)}`;
    return this.request(endpoint);
  }

  async getHuggingFaceModelInfo(name: string): Promise<any> {
    return this.request(`/huggingface/info/${encodeURIComponent(name)}`);
  }
}

// Text2Image generation parameters interface
export interface ImageGenerationParams {
  height?: number;
  width?: number;
  guidance_scale?: number;
  num_inference_steps?: number;
  max_sequence_length?: number;
  seed?: number;
}

// Export a singleton instance
export const assaiAPI = new AssAI_API();
