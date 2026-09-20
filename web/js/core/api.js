/**
 * The HTTP client.
 *
 * Every server error becomes an `ApiError` carrying a message written for a
 * student, because the server writes its `detail` fields that way. Views show
 * `error.message` directly and never have to invent wording for a failure they
 * did not anticipate.
 */

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  get isAuth() {
    return this.status === 401;
  }
  get isMissing() {
    return this.status === 404;
  }
  get isOffline() {
    return this.status === 0;
  }
}

const OFFLINE_MESSAGE =
  'You appear to be offline. Anything you have listened to is still saved.';

async function request(method, path, { body, signal, raw = false } = {}) {
  const options = {
    method,
    signal,
    credentials: 'same-origin',
    headers: {},
  };

  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers['content-type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new ApiError(OFFLINE_MESSAGE, 0, null);
  }

  if (raw) {
    if (!response.ok) throw new ApiError(await readError(response), response.status, null);
    return response;
  }

  if (response.status === 204) return null;

  let payload = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const message =
      (payload && typeof payload.detail === 'string' && payload.detail) ||
      'Something went wrong. Try that again.';
    throw new ApiError(message, response.status, payload);
  }

  return payload;
}

async function readError(response) {
  try {
    const data = await response.json();
    if (typeof data.detail === 'string') return data.detail;
  } catch {
    /* fall through to the generic message */
  }
  return 'Something went wrong. Try that again.';
}

const get = (path, options) => request('GET', path, options);
const post = (path, body, options) => request('POST', path, { ...options, body });
const patch = (path, body, options) => request('PATCH', path, { ...options, body });
const del = (path, options) => request('DELETE', path, options);

export const api = {
  ApiError,

  auth: {
    me: () => get('/api/auth/me'),
    signUp: (payload) => post('/api/auth/signup', payload),
    signIn: (payload) => post('/api/auth/signin', payload),
    signOut: () => post('/api/auth/signout', {}),
    updateMe: (payload) => patch('/api/auth/me', payload),
    modes: () => get('/api/auth/modes'),
  },

  spaces: {
    list: () => get('/api/spaces'),
    create: (payload) => post('/api/spaces', payload),
    get: (id) => get(`/api/spaces/${id}`),
    update: (id, payload) => patch(`/api/spaces/${id}`, payload),
    remove: (id) => del(`/api/spaces/${id}`),
    map: (id) => get(`/api/spaces/${id}/map`),
    analyse: (id) => post(`/api/spaces/${id}/analyse`, {}),
    job: (id, kind = 'analyse') => get(`/api/spaces/${id}/job?kind=${kind}`),
    conversation: (id) => get(`/api/spaces/${id}/conversation`),
    ask: (id, payload) => post(`/api/spaces/${id}/ask`, payload),
  },

  resources: {
    upload: (spaceId, file, title = '', onProgress) =>
      uploadWithProgress(`/api/spaces/${spaceId}/resources`, file, title, onProgress),
    addUrl: (spaceId, url) => post(`/api/spaces/${spaceId}/resources/url`, { url }),
    addNote: (spaceId, payload) => post(`/api/spaces/${spaceId}/resources/note`, payload),
    remove: (spaceId, resourceId) => del(`/api/spaces/${spaceId}/resources/${resourceId}`),
    setRole: (spaceId, resourceId, role) =>
      patch(`/api/spaces/${spaceId}/resources/${resourceId}`, { role }),
  },

  lessons: {
    get: (id) => get(`/api/lessons/${id}`),
  },

  sessions: {
    update: (id, payload) => patch(`/api/sessions/${id}`, payload),
    answer: (id, payload) => post(`/api/sessions/${id}/answer`, payload),
    complete: (id) => post(`/api/sessions/${id}/complete`, {}),
    anotherQuestion: (id) => post(`/api/sessions/${id}/question`, {}),
  },

  audio: {
    status: (lessonId, voice = '') =>
      get(`/api/audio/status/${lessonId}${voice ? `?voice=${encodeURIComponent(voice)}` : ''}`),
    prepare: (lessonId, voice = '') => post(`/api/audio/prepare/${lessonId}`, { voice }),
    downloadUrl: (lessonId, voice = '') =>
      `/api/audio/download/${lessonId}${voice ? `?voice=${encodeURIComponent(voice)}` : ''}`,
  },

  bookmarks: {
    list: (spaceId = '') => get(`/api/bookmarks${spaceId ? `?space_id=${spaceId}` : ''}`),
    add: (spaceId, payload) => post(`/api/spaces/${spaceId}/bookmarks`, payload),
    remove: (id) => del(`/api/bookmarks/${id}`),
  },

  dashboard: () => get('/api/dashboard'),
  progress: () => get('/api/progress'),

  transcribe: (blob) => {
    const form = new FormData();
    form.append('audio', blob, 'answer.webm');
    return post('/api/transcribe', form);
  },
};

/**
 * Upload with real progress.
 *
 * `fetch` cannot report upload progress, and a student adding a 20MB textbook
 * on a phone needs to see that something is happening, so this one call uses
 * XMLHttpRequest.
 */
function uploadWithProgress(url, file, title, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(event.loaded / event.total);
      }
    });

    xhr.addEventListener('load', () => {
      let payload = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
      } else {
        reject(
          new ApiError(
            (payload && payload.detail) || 'That file could not be added.',
            xhr.status,
            payload
          )
        );
      }
    });

    xhr.addEventListener('error', () => reject(new ApiError(OFFLINE_MESSAGE, 0, null)));
    xhr.addEventListener('abort', () => reject(new ApiError('Upload cancelled.', 0, null)));
    xhr.send(form);
  });
}
