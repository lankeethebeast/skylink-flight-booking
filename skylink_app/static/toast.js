// Toast Notification System
// Replaces alert() and shows styled slide-in notifications

(function() {
  // Create toast container
  const container = document.createElement('div');
  container.id = 'toastContainer';
  container.style.cssText = `
    position: fixed;
    top: 80px;
    right: 20px;
    z-index: 10000;
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 400px;
    width: calc(100% - 40px);
    pointer-events: none;
  `;
  document.body.appendChild(container);

  const icons = {
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️'
  };

  const colors = {
    success: { bg: '#f0fdf4', border: '#10b981', text: '#065f46', icon: '#10b981' },
    error: { bg: '#fef2f2', border: '#ef4444', text: '#991b1b', icon: '#ef4444' },
    warning: { bg: '#fffbeb', border: '#f59e0b', text: '#92400e', icon: '#f59e0b' },
    info: { bg: '#eff6ff', border: '#3b82f6', text: '#1e40af', icon: '#3b82f6' }
  };

  function showToast(message, type = 'info', duration = 4000) {
    const c = colors[type] || colors.info;
    const icon = icons[type] || icons.info;

    const toast = document.createElement('div');
    toast.style.cssText = `
      background: ${c.bg};
      border: 1px solid ${c.border};
      border-left: 4px solid ${c.border};
      border-radius: 10px;
      padding: 1rem 1.2rem;
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      box-shadow: 0 8px 30px rgba(0,0,0,0.12);
      transform: translateX(120%);
      transition: transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.3s ease;
      pointer-events: auto;
      cursor: pointer;
      position: relative;
    `;

    toast.innerHTML = `
      <span style="font-size: 1.2rem; flex-shrink: 0; margin-top: 1px;">${icon}</span>
      <div style="flex: 1; min-width: 0;">
        <div style="color: ${c.text}; font-weight: 600; font-size: 0.9rem; line-height: 1.4;">${message}</div>
      </div>
      <button onclick="this.parentElement.remove()" style="
        background: none; border: none; color: ${c.text}; cursor: pointer;
        font-size: 1.1rem; opacity: 0.5; padding: 0; line-height: 1; flex-shrink: 0;
      ">&times;</button>
    `;

    // Progress bar
    const progress = document.createElement('div');
    progress.style.cssText = `
      position: absolute;
      bottom: 0;
      left: 0;
      height: 3px;
      background: ${c.border};
      border-radius: 0 0 10px 0;
      width: 100%;
      transform-origin: left;
      animation: toastProgress ${duration}ms linear forwards;
    `;
    toast.appendChild(progress);

    container.appendChild(toast);

    // Slide in
    requestAnimationFrame(() => {
      toast.style.transform = 'translateX(0)';
    });

    // Click to dismiss
    toast.addEventListener('click', () => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(120%)';
      setTimeout(() => toast.remove(), 300);
    });

    // Auto-remove
    setTimeout(() => {
      if (toast.parentElement) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(120%)';
        setTimeout(() => toast.remove(), 300);
      }
    }, duration);

    return toast;
  }

  // Add progress bar animation CSS
  const style = document.createElement('style');
  style.textContent = `
    @keyframes toastProgress {
      from { transform: scaleX(1); }
      to { transform: scaleX(0); }
    }
  `;
  document.head.appendChild(style);

  // Expose globally
  window.showToast = showToast;

  // Override alert() with toast
  window._originalAlert = window.alert;
  window.alert = function(message) {
    showToast(message, 'info', 5000);
  };

  // Show Flask flash messages as toasts on page load
  document.addEventListener('DOMContentLoaded', () => {
    const flashMessages = document.querySelectorAll('.flash-message');
    flashMessages.forEach(el => {
      const type = el.dataset.type || 'info';
      const message = el.textContent.trim();
      if (message) {
        showToast(message, type, 5000);
      }
      el.remove();
    });
  });
})();