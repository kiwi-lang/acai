// API service for recipe management
// Use /api prefix to leverage Vite proxy and avoid CORS issues
const API_BASE_URL = "/api";

class AssAI_API {

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    // Development mode - make actual API calls
    const url = `${API_BASE_URL}${endpoint}`;
    const config: RequestInit = {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    };

    try {
      const response = await fetch(url, config);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('API request failed:', error);
      throw error;
    }
  }

}

// Export a singleton instance
export const assaiAPI = new AssAI_API();
