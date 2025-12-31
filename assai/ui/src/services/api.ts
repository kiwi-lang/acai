// API service for ASSAI - AI Multi-Modal Platform
// Use /api prefix to leverage Vite proxy and avoid CORS issues
import { ChatRequest, ChatResponse, Conversation, Message, ModelPlugin, MultimodalConversation, MultimodalMessage } from './types';

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

  // Multimodal chat endpoints
  async sendMultimodalMessage(
    conversation: MultimodalConversation,
    sessionId?: string,
    actionId?: number
  ): Promise<{ message: MultimodalMessage; conversation_id: string }> {
    const endpoint = '/multimodal/chat';
    const body: any = { conversation };
    if (sessionId) {
      body.session_id = sessionId;
    }
    if (actionId !== undefined) {
      body.action_id = actionId;
    }
    return this.request<{ message: MultimodalMessage; conversation_id: string }>(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
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

  // Unified model run endpoint - all models use Message format
  // Takes a single Message, returns Message response
  async runModel(
    message: Message,
    modelType: 'text2text' | 'text2image' | 'text2video' | 'image2mesh' | 'text2speech' | 'speech2text' | 'depth_estimation',
    params?: Record<string, any>,
    model?: string,
    sessionId?: string,
    actionId?: number
  ): Promise<{ message: Message }> {
    const endpoint = model
      ? `/${modelType}/model/run/${encodeURIComponent(model)}`
      : `/${modelType}/model/run`;

    const body: any = { message, ...params };
    if (sessionId) {
      body.session_id = sessionId;
    }
    if (actionId !== undefined) {
      body.action_id = actionId;
    }

    return this.request<{ message: Message }>(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  // Text2Image endpoint - uses unified Message format
  async generateImage(
    prompt: string,
    params?: ImageGenerationParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Build message if not provided
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'text',
        encoding: 'utf8',
        data: prompt
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'text2image', params, model, sessionId, actionId);
  }

  // Text2Video endpoint - uses unified Message format
  async generateVideo(
    prompt: string,
    params?: VideoGenerationParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Build message if not provided
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'text',
        encoding: 'utf8',
        data: prompt
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'text2video', params, model, sessionId, actionId);
  }

  // Text2Speech endpoint - uses unified Message format
  async generateSpeech(
    prompt: string,
    params?: SpeechGenerationParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Build message if not provided
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'text',
        encoding: 'utf8',
        data: prompt
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'text2speech', params, model, sessionId, actionId);
  }

  // Text2Text endpoint - uses unified Message format
  async generateText(
    prompt: string,
    params?: TextGenerationParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Build message if not provided
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'text',
        encoding: 'utf8',
        data: prompt
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'text2text', params, model, sessionId, actionId);
  }

  // Speech2Text endpoint - uses unified Message format
  async transcribeSpeech(
    audioDataUri: string,
    params?: SpeechRecognitionParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Build message if not provided
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'audio',
        encoding: 'data_url',
        data: audioDataUri
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'speech2text', params, model, sessionId, actionId);
  }

  // Image2Mesh endpoint - uses unified Message format
  async generateMesh(
    imageDataUri: string,
    params?: MeshGenerationParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Use provided message if it has image content, otherwise build from imageDataUri
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'image',
        encoding: 'data_url',
        data: imageDataUri
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'image2mesh', params, model, sessionId, actionId);
  }

  // DepthEstimation endpoint - uses unified Message format
  async estimateDepth(
    imageDataUri: string,
    params?: DepthEstimationParams,
    model?: string,
    sessionId?: string,
    actionId?: number,
    message?: Message
  ): Promise<{ message: Message }> {
    // Use provided message if it has image content, otherwise build from imageDataUri
    const msg: Message = message || {
      id: Date.now(),
      role: 'user',
      content: {
        kind: 'image',
        encoding: 'data_url',
        data: imageDataUri
      },
      timestamp: new Date().toISOString()
    };

    return this.runModel(msg, 'depth_estimation', params, model, sessionId, actionId);
  }

  // Telemetry endpoint
  async getTelemetry(signal?: AbortSignal): Promise<{
    cpu: { memory: [number, number]; load: number };
    gpu: Record<string, { memory: [number, number]; load: number; temp: number; power: number }>;
    network?: {
      bytes_recv: number;
      bytes_sent: number;
      packets_recv: number;
      packets_sent: number;
      errin: number;
      errout: number;
      dropin: number;
      dropout: number;
    };
    disk?: {
      busy_time: number;
      read_bytes: number;
      read_count: number;
      read_time: number;
      write_count: number;
      write_time: number;
    };
  }> {
    return this.request('/telemetry', { signal });
  }

  // Model settings endpoints - task-specific
  async getModelSettingsSpec(
    taskType: 'text2image' | 'text2text' | 'text2video' | 'image2mesh' | 'text2speech' | 'speech2text' | 'depth_estimation',
    modelName?: string
  ): Promise<Record<string, { type: 'int' | 'float'; min?: number | null; max?: number | null; default: number }>> {
    const endpoint = modelName
      ? `/${taskType}/model/settings/${encodeURIComponent(modelName)}`
      : `/${taskType}/model/settings`;
    return this.request(endpoint);
  }

  async getModelSettings(
    taskType: 'text2image' | 'text2text' | 'text2video' | 'image2mesh' | 'text2speech' | 'speech2text' | 'depth_estimation',
    modelName?: string
  ): Promise<Record<string, number>> {
    try {
      const endpoint = modelName
        ? `/${taskType}/model/settings/${encodeURIComponent(modelName)}/values`
        : `/${taskType}/model/settings/values`;
      return await this.request<Record<string, number>>(endpoint);
    } catch (error) {
      // If endpoint doesn't exist or returns 404, return empty object
      return {};
    }
  }

  async saveModelSettings(
    taskType: 'text2image' | 'text2text' | 'text2video' | 'image2mesh' | 'text2speech' | 'speech2text' | 'depth_estimation',
    modelName: string,
    settings: Record<string, number>
  ): Promise<void> {
    const endpoint = `/${taskType}/model/settings/${encodeURIComponent(modelName)}/values`;
    return this.request(endpoint, {
      method: 'POST',
      body: JSON.stringify(settings),
    });
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

  // Loaded models endpoints
  async getLoadedModels(): Promise<{
    system: {
      gpu: Record<string, {
        memory: [number, number];
      }>;
    };
    torch: {
      allocated: number;
      reserved: number;
    };
    models: Record<string, { memory_usage: number; load_time: number }>;
  }> {
    return this.request('/huggingface/loaded/models/list');
  }

  async removeLoadedModel(name: string): Promise<{ success: boolean; message: string }> {
    return this.request(`/huggingface/loaded/models/remove/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
  }

  // Text2Text model endpoints
  async listText2TextModels(): Promise<string[]> {
    return this.request<string[]>('/text2text/model/list');
  }

  // Text2Image model endpoints
  async listText2ImageModels(): Promise<string[]> {
    return this.request<string[]>('/text2image/model/list');
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

// Text2Video generation parameters interface
export interface VideoGenerationParams {
  height?: number;
  width?: number;
  num_frames?: number;
  num_inference_steps?: number;
  seed?: number;
}

// Image2Mesh generation parameters interface
export interface MeshGenerationParams {
  guidance_scale?: number;
  num_inference_steps?: number;
  seed?: number;
}

// Text2Speech generation parameters interface
export interface SpeechGenerationParams {
  speed?: number;
  pitch?: number;
  sample_rate?: number;
}

// Text2Text generation parameters interface
export interface TextGenerationParams {
  max_length?: number;
  max_new_tokens?: number;
  temperature?: number;
  top_p?: number;
  top_k?: number;
  repetition_penalty?: number;
  do_sample?: boolean;
}

// Speech2Text recognition parameters interface
export interface SpeechRecognitionParams {
  language?: string | null;
  task?: 'transcribe' | 'translate';
}

// DepthEstimation parameters interface
export interface DepthEstimationParams {
  colormap?: 'jet' | 'viridis' | 'plasma' | 'inferno' | 'magma' | 'turbo';
}

// Export a singleton instance
export const assaiAPI = new AssAI_API();
