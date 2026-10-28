/**
 * ImageLabel 原生 JS SDK（无依赖，浏览器 + Node 18+ 通用）
 *
 * 直接调用 FastAPI 后端（本项目前端默认 http://10.11.82.221:8000），Bearer token 由 SDK 自己保管。
 *
 * 用法（浏览器，经典 script 标签）：
 *   <script src="/static/sdk.js"></script>
 *   <script>
 *     const sdk = new ImageLabelSDK({ baseUrl: 'http://10.11.82.221:8000' });
 *     await sdk.signin('alice', '12345678');          // 登录后 token 自动写进 sdk
 *     const projects = await sdk.listVisibleProjects();
 *     const page = await sdk.listAnnotations(projects[0].id, { limit: 20 });
 *   </script>
 *
 * 用法（Node 18+ / ESM 项目）：
 *   const { ImageLabelSDK, SDKError } = require('./flask/static/sdk.js');
 *   const sdk = new ImageLabelSDK({ baseUrl: 'http://10.11.82.221:8000' });
 *
 * token 持久化（浏览器）：
 *   const sdk = new ImageLabelSDK({ storage: localStorage, tokenKey: 'il.token' });
 *
 * 所有失败都抛 SDKError（status / detail 与后端 `{"detail": ...}` 对齐）。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    // 只能调一次 factory：调两次会造出两个互不相等的 SDKError 类，
    // 于是 `err instanceof SDKError` 永远为 false，外层的错误翻译全部失效。
    const lib = factory();
    root.ImageLabelSDK = lib.ImageLabelSDK;
    root.SDKError = lib.SDKError;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  class SDKError extends Error {
    /** @param {number} status @param {*} detail @param {*} body */
    constructor(status, detail, body) {
      const text =
        typeof detail === 'string' ? detail : detail == null ? '' : JSON.stringify(detail);
      super(text || `HTTP ${status}`);
      this.name = 'SDKError';
      this.status = status;
      this.detail = detail;
      this.body = body;
    }
  }

  const enc = encodeURIComponent;

  function query(params) {
    if (!params) return '';
    const parts = [];
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue;
      parts.push(`${enc(key)}=${enc(String(value))}`);
    }
    return parts.length ? `?${parts.join('&')}` : '';
  }

  // 后端各上传端点的表单字段名并不统一：
  //   /projects/{id}/images  → files（复数，可多张）
  //   /groups/{id}/images    → file（单数，一次一张）
  //   /auth/me/avatar        → file
  // 所以这里把字段名做成参数，避免调用方再自己拼 FormData。
  function formFiles(files, field = 'files') {
    // files 支持：
    //   File / Blob（直接 append）
    //   { filename, data: Uint8Array|ArrayBuffer, type? }
    const form = new FormData();
    for (const item of files) {
      if (typeof Blob !== 'undefined' && item instanceof Blob) {
        form.append(field, item, item.name || 'image.bin');
        continue;
      }
      if (item && item.data) {
        const type = item.type || 'application/octet-stream';
        const blob = new Blob([item.data], { type });
        form.append(field, blob, item.filename || 'image.bin');
        continue;
      }
      throw new TypeError('上传需要 File/Blob 或 {filename, data, type}');
    }
    return form;
  }

  class ImageLabelSDK {
    /**
     * @param {object} [options]
     * @param {string} [options.baseUrl]  后端地址，默认 http://10.11.82.221:8000
     * @param {string} [options.token]    初始 token
     * @param {number} [options.timeout]  毫秒，默认 15000
     * @param {object} [options.storage]  例如 window.localStorage，用于持久化 token
     * @param {string} [options.tokenKey] storage 的键名，默认 imagelabel.token
     * @param {Function} [options.fetch]  自定义 fetch（测试/嵌入）
     * @param {Function} [options.onUnauthorized] 401 时的回调（清登录态/跳登录页）
     */
    constructor(options = {}) {
      this.baseUrl = String(options.baseUrl || 'http://10.11.82.221:8000').replace(/\/+$/, '');
      this.timeout = options.timeout ?? 15000;
      this.storage = options.storage || null;
      this.tokenKey = options.tokenKey || 'imagelabel.token';
      this.onUnauthorized = options.onUnauthorized || null;
      this.fetch = options.fetch || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
      if (!this.fetch) throw new Error('当前环境没有 fetch，请注入 options.fetch');
      this.token = options.token || null;
      if (!this.token && this.storage) {
        try {
          this.token = this.storage.getItem(this.tokenKey) || null;
        } catch (_) {
          this.token = null;
        }
      }
    }

    /** 设置/清除 token；传 null 表示退出。 */
    setToken(token) {
      this.token = token || null;
      if (this.storage) {
        try {
          if (this.token) this.storage.setItem(this.tokenKey, this.token);
          else this.storage.removeItem(this.tokenKey);
        } catch (_) {
          /* 隐私模式等场景忽略 */
        }
      }
      return this;
    }

    getToken() {
      return this.token;
    }

    // ---- 底层 ----

    /**
     * @private
     * @returns {Promise<{status:number, body:any, headers:Headers, blob?:Blob, response:Response}>}
     */
    async _raw(method, path, { json, params, form, headers: extraHeaders, raw } = {}) {
      const headers = Object.assign({ Accept: raw ? '*/*' : 'application/json' }, extraHeaders || {});
      if (this.token) headers.Authorization = `Bearer ${this.token}`;
      let body;
      if (form) {
        body = form; // 让浏览器自己带 multipart boundary
      } else if (json !== undefined) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(json);
      }

      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const timer = controller
        ? setTimeout(() => controller.abort(), this.timeout)
        : null;
      let response;
      try {
        response = await this.fetch(`${this.baseUrl}${path}${query(params)}`, {
          method,
          headers,
          body,
          signal: controller ? controller.signal : undefined,
        });
      } catch (exc) {
        if (exc && exc.name === 'AbortError') {
          throw new SDKError(408, `请求超时（${this.timeout}ms）：${method} ${path}`);
        }
        throw new SDKError(502, `后端不可用：${exc && exc.message ? exc.message : exc}`);
      } finally {
        if (timer) clearTimeout(timer);
      }

      if (response.status === 204) {
        return { status: 204, body: null, headers: response.headers, response };
      }

      let data = null;
      if (raw) {
        data = await response.blob();
      } else {
        const text = await response.text();
        try {
          data = text ? JSON.parse(text) : null;
        } catch (_) {
          data = text;
        }
      }

      if (!response.ok) {
        const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
        if (response.status === 401 && this.onUnauthorized) {
          try {
            this.onUnauthorized(detail);
          } catch (_) {
            /* 回调里的异常不能吞掉原错误 */
          }
        }
        throw new SDKError(response.status, detail, data);
      }

      return { status: response.status, body: data, headers: response.headers, response };
    }

    async _json(method, path, opts) {
      const out = await this._raw(method, path, opts);
      return out.body;
    }

    /** GET /_alive，探活。 */
    health() {
      return this._json('GET', '/_alive');
    }

    // ---- 认证 ----

    /** 注册；成功后自动登录（token 写回 SDK）。返回 { access_token, token_type, expires_in, account }。 */
    async signup(username, password, { email, phone } = {}) {
      const payload = { username, password };
      if (email) payload.email = email;
      if (phone) payload.phone = phone;
      const data = await this._json('POST', '/auth/signup', { json: payload });
      if (data && data.access_token) this.setToken(data.access_token);
      return data;
    }

    /** 登录；成功后自动登录。返回 account 对象。 */
    async signin(username, password) {
      const data = await this._json('POST', '/auth/signin', {
        json: { username, password },
      });
      if (data && data.access_token) this.setToken(data.access_token);
      return (data && data.account) || data;
    }

    /** 登出当前这枚 token（只作废本设备的 token）。 */
    async signout() {
      try {
        await this._json('POST', '/auth/signout');
      } finally {
        this.setToken(null);
      }
      return null;
    }

    /** 当前账号。 */
    me() {
      return this._json('GET', '/auth/me');
    }

    /** 改资料：{ email, phone }，显式传 null 清空。 */
    updateMe(fields) {
      return this._json('PATCH', '/auth/me', { json: fields });
    }

    /** 改密码；旧密码错误抛 SDKError(400)。改完其他设备 token 全失效。 */
    async changePassword(oldPassword, newPassword) {
      await this._raw('POST', '/auth/me/password', {
        json: { old_password: oldPassword, new_password: newPassword },
      });
    }

    // ---- 个人空间的项目（/space） ----

    /** 我的个人空间项目列表。 */
    listProjects({ includeArchived = false } = {}) {
      return this._json('GET', '/space/projects', {
        params: includeArchived ? { include_archived: 'true' } : undefined,
      });
    }

    /** 在个人空间建项目。 */
    createProject(name, { description, defaultReviewer, managerAccountId } = {}) {
      const payload = { name };
      if (description) payload.description = description;
      if (defaultReviewer) payload.default_reviewer = defaultReviewer;
      if (managerAccountId != null) payload.manager_account_id = managerAccountId;
      return this._json('POST', '/space/projects', { json: payload });
    }

    archiveProject(projectId) {
      return this._json('POST', `/space/projects/${projectId}/archive`);
    }

    restoreProject(projectId) {
      return this._json('POST', `/space/projects/${projectId}/restore`);
    }

    deleteProject(projectId) {
      return this._raw('DELETE', `/space/projects/${projectId}`);
    }

    // ---- 团队 / 组织（/groups） ----

    listGroups() {
      return this._json('GET', '/groups');
    }

    createGroup(name, { type = 'team', parentId, description } = {}) {
      const payload = { name, type };
      if (parentId != null) payload.parent_id = parentId;
      if (description) payload.description = description;
      return this._json('POST', '/groups', { json: payload });
    }

    getGroup(groupId) {
      return this._json('GET', `/groups/${groupId}`);
    }

    updateGroup(groupId, fields) {
      return this._json('PATCH', `/groups/${groupId}`, { json: fields });
    }

    deleteGroup(groupId) {
      return this._raw('DELETE', `/groups/${groupId}`);
    }

    transferGroup(groupId, newOwnerAccountId) {
      return this._json('POST', `/groups/${groupId}/transfer`, {
        json: { new_owner_account_id: newOwnerAccountId },
      });
    }

    listGroupMembers(groupId) {
      return this._json('GET', `/groups/${groupId}/members`);
    }

    addGroupMember(groupId, accountId, { role = 'member' } = {}) {
      return this._json('POST', `/groups/${groupId}/members`, {
        json: { account_id: accountId, role },
      });
    }

    updateGroupMemberRole(groupId, accountId, role) {
      return this._json('PATCH', `/groups/${groupId}/members/${accountId}`, {
        json: { role },
      });
    }

    removeGroupMember(groupId, accountId) {
      return this._raw('DELETE', `/groups/${groupId}/members/${accountId}`);
    }

    leaveGroup(groupId) {
      return this._raw('POST', `/groups/${groupId}/leave`);
    }

    // ---- 我可见的项目（个人空间 + 团队 + 被邀请） ----

    listVisibleProjects({ includeArchived = false } = {}) {
      return this._json('GET', '/projects', {
        params: includeArchived ? { include_archived: 'true' } : undefined,
      });
    }

    getProject(projectId) {
      return this._json('GET', `/projects/${projectId}`);
    }

    /** 项目生命周期（manager 或空间 owner/admin）。 */
    updateProject(projectId, fields) {
      return this._json('PATCH', `/projects/${projectId}`, { json: fields });
    }

    archiveVisibleProject(projectId) {
      return this._json('POST', `/projects/${projectId}/archive`);
    }

    restoreVisibleProject(projectId) {
      return this._json('POST', `/projects/${projectId}/restore`);
    }

    deleteVisibleProject(projectId) {
      return this._raw('DELETE', `/projects/${projectId}`);
    }

    /** 项目统计：任务/图片/标注分布与复核通过率。 */
    projectStats(projectId) {
      return this._json('GET', `/projects/${projectId}/stats`);
    }

    // ---- 项目成员 ----

    listProjectMembers(projectId) {
      return this._json('GET', `/projects/${projectId}/members`);
    }

    addProjectMember(projectId, accountId, { role = 'annotator', expiresAt, sourceGroupId } = {}) {
      const payload = { account_id: accountId, role };
      if (expiresAt) payload.expires_at = expiresAt;
      if (sourceGroupId != null) payload.source_group_id = sourceGroupId;
      return this._json('POST', `/projects/${projectId}/members`, { json: payload });
    }

    updateProjectMember(projectId, accountId, roles) {
      return this._json('PATCH', `/projects/${projectId}/members/${accountId}`, {
        json: { roles },
      });
    }

    removeProjectMember(projectId, accountId) {
      return this._raw('DELETE', `/projects/${projectId}/members/${accountId}`);
    }

    // ---- 类目（检测提示词） ----

    listCategories(projectId, { includeDisabled = false } = {}) {
      return this._json('GET', `/projects/${projectId}/categories`, {
        params: includeDisabled ? { include_disabled: 'true' } : undefined,
      });
    }

    createCategory(projectId, { zhName, enName = '', thresholdOverride, sortOrder = 0, enabled = true }) {
      const payload = { zh_name: zhName, sort_order: sortOrder, enabled };
      if (enName && enName.trim()) payload.en_name = enName;
      if (thresholdOverride != null) payload.threshold_override = thresholdOverride;
      return this._json('POST', `/projects/${projectId}/categories`, { json: payload });
    }

    updateCategory(projectId, categoryId, fields) {
      return this._json('PATCH', `/projects/${projectId}/categories/${categoryId}`, {
        json: fields,
      });
    }

    deleteCategory(projectId, categoryId) {
      return this._raw('DELETE', `/projects/${projectId}/categories/${categoryId}`);
    }

    // ---- 自动标注参数 ----

    getAutoConfig(projectId) {
      return this._json('GET', `/projects/${projectId}/auto-config`);
    }

    updateAutoConfig(projectId, fields) {
      return this._json('PATCH', `/projects/${projectId}/auto-config`, { json: fields });
    }

    // ---- 标注 ----

    /**
     * 项目标注分页：{ items, total, offset, limit }。
     * 过滤：task_id / task_image_id / category_id / is_current。
     */
    listAnnotations(projectId, { taskId, taskImageId, categoryId, isCurrent = true, offset = 0, limit = 50 } = {}) {
      return this._json('GET', `/projects/${projectId}/annotations`, {
        params: {
          offset,
          limit,
          task_id: taskId,
          task_image_id: taskImageId,
          category_id: categoryId,
          is_current: isCurrent === null ? undefined : String(isCurrent),
        },
      });
    }

    /** 导出 COCO JSON：返回 { blob, filename }。 */
    async exportAnnotations(projectId, { format = 'coco' } = {}) {
      const out = await this._raw('GET', `/projects/${projectId}/annotations/export`, {
        params: { format },
        raw: true,
      });
      return { blob: out.body, filename: filenameOf(out.headers) };
    }

    // ---- 项目图片 ----

    listProjectImages(projectId, { offset = 0, limit = 50 } = {}) {
      return this._json('GET', `/projects/${projectId}/images`, { params: { offset, limit } });
    }

    /** 批量上传：files 为 File/Blob 数组或 [{filename, data, type}]。 */
    uploadProjectImages(projectId, files) {
      return this._json('POST', `/projects/${projectId}/images`, {
        form: formFiles(files),
      });
    }

    removeProjectImage(projectId, imageId) {
      return this._raw('DELETE', `/projects/${projectId}/images/${imageId}`);
    }

    /** 取图片字节：{ blob, contentType }。 */
    async imageContent(imageId) {
      const out = await this._raw('GET', `/images/${imageId}/content`, { raw: true });
      return {
        blob: out.body,
        contentType: out.headers.get('content-type') || 'application/octet-stream',
      };
    }

    /** 组空间图片：上传/列表/用量。 */
    listGroupImages(groupId, { includeDeleted = false } = {}) {
      return this._json('GET', `/groups/${groupId}/images`, {
        params: includeDeleted ? { include_deleted: 'true' } : undefined,
      });
    }

    /**
     * 组空间图片上传。后端这个端点一次只收一个 `file`，
     * 所以这里接受单张或数组，内部逐张提交，统一返回结果数组。
     */
    async uploadGroupImages(groupId, files) {
      const list = Array.isArray(files) ? files : [files];
      const out = [];
      for (const item of list) {
        out.push(
          await this._json('POST', `/groups/${groupId}/images`, {
            form: formFiles([item], 'file'),
          })
        );
      }
      return out;
    }

    /** 组空间图片用量：{ used_images, used_bytes, quota_images, quota_storage_mb, ... }。 */
    groupImageUsage(groupId) {
      return this._json('GET', `/groups/${groupId}/images/usage`);
    }

    /** 小组聊天。远端后端未部署时会明确抛 404，由前端展示能力缺口。 */
    listGroupMessages(groupId, { afterId, limit = 100 } = {}) {
      return this._json('GET', `/groups/${groupId}/messages`, {
        params: { after_id: afterId, limit },
      });
    }

    sendGroupMessage(groupId, content) {
      return this._json('POST', `/groups/${groupId}/messages`, {
        json: { content },
      });
    }

    // ---- 头像（Vision Studio 本地扩展，官方 SDK 未覆盖） ----

    /** 上传/替换头像，返回更新后的账号（含 has_avatar）。 */
    uploadAvatar(file) {
      return this._json('POST', '/auth/me/avatar', { form: formFiles([file], 'file') });
    }

    /** 移除头像，回退到首字头像。 */
    deleteAvatar() {
      return this._raw('DELETE', '/auth/me/avatar');
    }

    /** 取头像字节；没设置过头像时抛 SDKError(404)，调用方回退到首字。 */
    async avatarBlob(accountId) {
      const out = await this._raw('GET', `/accounts/${accountId}/avatar`, { raw: true });
      return { blob: out.body, contentType: out.headers.get('content-type') || 'image/png' };
    }

    // ---- 企业空间（Vision Studio 本地扩展，官方后端尚未提供） ----
    // 真实后端没有这组端点时会明确抛 404，前端据此展示「后端未开放」而不是崩掉。

    listEnterprises() {
      return this._json('GET', '/enterprises');
    }

    createEnterprise(name, email) {
      return this._json('POST', '/enterprises', { json: { name, email } });
    }

    getEnterprise(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}`);
    }

    updateEnterprise(enterpriseId, fields) {
      return this._json('PATCH', `/enterprises/${enterpriseId}`, { json: fields });
    }

    deleteEnterprise(enterpriseId) {
      return this._raw('DELETE', `/enterprises/${enterpriseId}`);
    }

    /** 凭企业码申请加入。管理员审批前状态是 pending。 */
    joinEnterprise(code, message) {
      return this._json('POST', '/enterprises/join', { json: { code, message } });
    }

    listEnterpriseRequests(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/requests`);
    }

    /** decision: approve | reject */
    decideEnterpriseRequest(enterpriseId, accountId, decision) {
      return this._json('POST', `/enterprises/${enterpriseId}/requests/${accountId}/${decision}`);
    }

    listEnterpriseMembers(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/members`);
    }

    inviteEnterpriseMember(enterpriseId, username, role = 'member') {
      return this._json('POST', `/enterprises/${enterpriseId}/members`, { json: { username, role } });
    }

    updateEnterpriseMember(enterpriseId, accountId, fields) {
      return this._json('PATCH', `/enterprises/${enterpriseId}/members/${accountId}`, { json: fields });
    }

    removeEnterpriseMember(enterpriseId, accountId) {
      return this._raw('DELETE', `/enterprises/${enterpriseId}/members/${accountId}`);
    }

    leaveEnterprise(enterpriseId) {
      return this._raw('POST', `/enterprises/${enterpriseId}/leave`);
    }

    listEnterpriseImages(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/images`);
    }

    /** 企业图库上传：后端收复数 `files`，与项目上传一致。 */
    uploadEnterpriseImages(enterpriseId, files) {
      const list = Array.isArray(files) ? files : [files];
      return this._json('POST', `/enterprises/${enterpriseId}/images`, { form: formFiles(list, 'files') });
    }

    enterpriseUsage(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/usage`);
    }

    listEnterpriseReview(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/review`);
    }

    /** decision: approve | reject */
    reviewEnterpriseImage(enterpriseId, imageId, decision, note) {
      return this._json('POST', `/enterprises/${enterpriseId}/images/${imageId}/review`, {
        json: { decision, note },
      });
    }

    enterpriseRanking(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/ranking`);
    }

    getEnterpriseNotice(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/notice`);
    }

    setEnterpriseNotice(enterpriseId, text) {
      return this._json('PUT', `/enterprises/${enterpriseId}/notice`, { json: { text } });
    }

    listEnterpriseLogs(enterpriseId) {
      return this._json('GET', `/enterprises/${enterpriseId}/logs`);
    }

    enterpriseReport(enterpriseId, date) {
      return this._json('GET', `/enterprises/${enterpriseId}/report`, { params: { date } });
    }

    enterpriseHeartbeat(enterpriseId, invisible) {
      return this._json('POST', `/enterprises/${enterpriseId}/heartbeat`, { json: { invisible } });
    }

    // ---- 通用底层（Vision Studio 本地扩展） ----

    /**
     * 公开的底层请求，给尚未封装成方法的端点用。
     * 走同一套超时、token、错误处理，不要在业务代码里另写 fetch。
     */
    request(method, path, opts) {
      return this._json(method, path, opts);
    }

    deleteImage(imageId) {
      return this._raw('DELETE', `/images/${imageId}`);
    }

    // ---- 任务 ----

    /**
     * 项目任务列表。可传 status / mode / assigneeAccountId / assignedToMe。
     */
    listProjectTasks(projectId, { status, mode, assigneeAccountId, assignedToMe = false } = {}) {
      return this._json('GET', `/projects/${projectId}/tasks`, {
        params: {
          status,
          mode,
          assignee_account_id: assigneeAccountId,
          assigned_to_me: assignedToMe ? 'true' : undefined,
        },
      });
    }

    /**
     * 建任务。mode='auto' 走模型自动标注；mode='manual' 交给标注员在标注工作台里画框。
     * dueAt 用 ISO 字符串（后端是 naive UTC）。
     */
    createTask(projectId, { name, reviewerAccountId, imageIds, mode = 'auto', assigneeAccountId, priority = 0, dueAt, note } = {}) {
      const payload = { project_id: projectId, name, mode, priority };
      if (reviewerAccountId != null) payload.reviewer_account_id = reviewerAccountId;
      if (imageIds && imageIds.length) payload.image_ids = imageIds;
      if (assigneeAccountId != null) payload.assignee_account_id = assigneeAccountId;
      if (dueAt) payload.due_at = dueAt;
      if (note) payload.note = note;
      return this._json('POST', `/projects/${projectId}/tasks`, { json: payload });
    }

    getTask(projectId, taskId) {
      return this._json('GET', `/projects/${projectId}/tasks/${taskId}`);
    }

    /** PATCH 协作字段（name/assignee_account_id/priority/due_at/note；null 表示清空）。 */
    updateTask(projectId, taskId, fields) {
      return this._json('PATCH', `/projects/${projectId}/tasks/${taskId}`, { json: fields });
    }

    attachTaskImages(projectId, taskId, imageIds) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/images`, {
        json: { image_ids: imageIds },
      });
    }

    submitTask(projectId, taskId) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/submit`);
    }

    retryTask(projectId, taskId) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/retry`);
    }

    cancelTask(projectId, taskId) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/cancel`);
    }

    /** manual 任务：所有图标注完成后结束标注，进入复核。 */
    completeTask(projectId, taskId) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/complete`);
    }

    requeueTask(projectId, taskId) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/requeue`);
    }

    resubmitTask(projectId, taskId) {
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/resubmit`);
    }

    // ---- 复核 ----

    listTaskReviews(projectId, taskId) {
      return this._json('GET', `/projects/${projectId}/tasks/${taskId}/reviews`);
    }

    /**
     * 任务级裁决：decision ∈ approve / reject_fix / reject_reannotate。
     * approve 前要保证所有图都 review_status='approved'。
     */
    submitTaskReview(projectId, taskId, { decision, comment } = {}) {
      const payload = { decision };
      if (comment) payload.comment = comment;
      return this._json('POST', `/projects/${projectId}/tasks/${taskId}/review`, {
        json: payload,
      });
    }

    reviewTaskImage(projectId, taskId, taskImageId, { approved, reason } = {}) {
      const payload = { approved: !!approved };
      if (reason) payload.reason = reason;
      return this._json(
        'POST',
        `/projects/${projectId}/tasks/${taskId}/images/${taskImageId}/review`,
        { json: payload }
      );
    }

    /**
     * 人工修正某张图的标注（整组覆盖）。
     * items: [{ label, category_id, annotation_type:'bbox', payload:{box:[x0,y0,x1,y1], score?}, confidence }]
     */
    replaceTaskImageAnnotations(projectId, taskId, taskImageId, items, { source = 'manual' } = {}) {
      return this._json(
        'PUT',
        `/projects/${projectId}/tasks/${taskId}/images/${taskImageId}/annotations`,
        { json: { items, source } }
      );
    }

    // ---- 我的任务（跨项目） ----

    /** role: assignee | submitter | reviewer | any */
    myTasks({ role = 'assignee', status, mode, limit = 100 } = {}) {
      return this._json('GET', '/me/tasks', { params: { role, status, mode, limit } });
    }

    // ---- 站内通知 ----

    listNotifications({ unreadOnly = false, offset = 0, limit = 20 } = {}) {
      return this._json('GET', '/notifications', {
        params: { unread_only: unreadOnly ? 'true' : undefined, offset, limit },
      });
    }

    unreadCount() {
      return this._json('GET', '/notifications/unread-count');
    }

    markNotificationRead(notificationId) {
      return this._json('POST', `/notifications/${notificationId}/read`);
    }

    markAllNotificationsRead() {
      return this._json('POST', '/notifications/read-all');
    }

    // ---- 账号搜索 ----

    /** 按用户名/邮箱片段找人（排除自己），用于邀请/指派。 */
    searchAccounts(q, { limit = 10 } = {}) {
      return this._json('GET', '/accounts/search', { params: { q, limit } });
    }

    // ---- 邀请 ----

    listInvitations({ status = 'pending' } = {}) {
      return this._json('GET', '/invitations', { params: { status } });
    }

    inviteToProject(projectId, { role, inviteeAccountId, inviteeEmail }) {
      return this._json('POST', `/projects/${projectId}/invitations`, {
        json: this._inviteBody({ role, inviteeAccountId, inviteeEmail }),
      });
    }

    inviteToGroup(groupId, { role, inviteeAccountId, inviteeEmail }) {
      return this._json('POST', `/groups/${groupId}/invitations`, {
        json: this._inviteBody({ role, inviteeAccountId, inviteeEmail }),
      });
    }

    listProjectInvitations(projectId, { status = 'pending' } = {}) {
      return this._json('GET', `/projects/${projectId}/invitations`, { params: { status } });
    }

    listGroupInvitations(groupId, { status = 'pending' } = {}) {
      return this._json('GET', `/groups/${groupId}/invitations`, { params: { status } });
    }

    acceptInvitation(invitationId) {
      return this._json('POST', `/invitations/${invitationId}/accept`);
    }

    rejectInvitation(invitationId) {
      return this._json('POST', `/invitations/${invitationId}/reject`);
    }

    revokeInvitation(invitationId) {
      return this._raw('DELETE', `/invitations/${invitationId}`);
    }

    _inviteBody({ role, inviteeAccountId, inviteeEmail }) {
      const payload = { role };
      if (inviteeAccountId != null) payload.invitee_account_id = inviteeAccountId;
      if (inviteeEmail) payload.invitee_email = inviteeEmail;
      return payload;
    }

    // ---- 平台管理（platform_role='admin'） ----

    adminStats() {
      return this._json('GET', '/admin/stats');
    }

    adminAccounts({ search, platformRole, offset = 0, limit = 50 } = {}) {
      return this._json('GET', '/admin/accounts', {
        params: { search, platform_role: platformRole, offset, limit },
      });
    }

    adminAccount(accountId) {
      return this._json('GET', `/admin/accounts/${accountId}`);
    }

    setPlatformRole(accountId, role) {
      return this._json('PATCH', `/admin/accounts/${accountId}/role`, {
        json: { platform_role: role },
      });
    }

    async resetAccountPassword(accountId, newPassword) {
      await this._raw('POST', `/admin/accounts/${accountId}/reset-password`, {
        json: { new_password: newPassword },
      });
    }

    adminGroups({ search, groupType, includeDeleted = false, offset = 0, limit = 50 } = {}) {
      return this._json('GET', '/admin/groups', {
        params: {
          search,
          group_type: groupType,
          include_deleted: String(includeDeleted),
          offset,
          limit,
        },
      });
    }

    adminProjects({ search, status, includeDeleted = false, offset = 0, limit = 50 } = {}) {
      return this._json('GET', '/admin/projects', {
        params: {
          search,
          status,
          include_deleted: String(includeDeleted),
          offset,
          limit,
        },
      });
    }

    adminAuditLogs({ actionPrefix, actorAccountId, groupId, resourceType, offset = 0, limit = 50 } = {}) {
      return this._json('GET', '/admin/audit-logs', {
        params: {
          action_prefix: actionPrefix,
          actor_account_id: actorAccountId,
          group_id: groupId,
          resource_type: resourceType,
          offset,
          limit,
        },
      });
    }

    getStoragePolicy() {
      return this._json('GET', '/admin/storage-policy');
    }

    updateStoragePolicy(fields) {
      return this._json('PUT', '/admin/storage-policy', { json: fields });
    }

    getModelConfig() {
      return this._json('GET', '/admin/model-config');
    }

    updateModelConfig(fields) {
      return this._json('PUT', '/admin/model-config', { json: fields });
    }

    // ---- 存储浏览器（/storage） ----

    storageInfo() {
      return this._json('GET', '/storage/info');
    }

    storageEntries(path = '', { recursive = false } = {}) {
      return this._json('GET', '/storage/entries', {
        params: { path, recursive: String(recursive) },
      });
    }

    storageStat(path) {
      return this._json('GET', '/storage/stat', { params: { path } });
    }

    storageUsage(path = '') {
      return this._json('GET', '/storage/usage', { params: { path } });
    }

    createStorageDirectory(path) {
      return this._json('POST', '/storage/directories', { json: { path } });
    }

    moveStoragePath(src, dst) {
      return this._json('POST', '/storage/move', { json: { src, dst } });
    }

    deleteStoragePath(path, { recursive = false } = {}) {
      return this._raw('DELETE', '/storage/entries', {
        params: { path, recursive: String(recursive) },
      });
    }

    /** 取存储对象字节：{ blob, contentType }。 */
    async storageDownload(path) {
      const out = await this._raw('GET', '/storage/download', { params: { path }, raw: true });
      return {
        blob: out.body,
        contentType: out.headers.get('content-type') || 'application/octet-stream',
      };
    }
  }

  function filenameOf(headers) {
    const disposition = headers.get('content-disposition') || '';
    const marker = 'filename="';
    const index = disposition.indexOf(marker);
    if (index >= 0) {
      const rest = disposition.slice(index + marker.length);
      const name = rest.split('"')[0];
      if (name) return name;
    }
    return 'export.json';
  }

  return { ImageLabelSDK, SDKError };
});
