module.exports = {
  content: [
    './app/templates/transcribe.html',
    './app/templates/glm-3.html',
    './app/templates/transcribe/**/*.html',
    './app/static/js/transcribe/**/*.js',
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['Fraunces', 'serif'],
        sans: ['DM Sans', 'sans-serif'],
      },
      colors: {
        cream: '#FAF8F5',
        parchment: '#F5F1EB',
        stone: '#E8E4DD',
        slate: '#2D3748',
        ink: '#1A202C',
        teal: {
          deep: '#1D4F5E',
          muted: '#3D7A8C',
          soft: '#5BA3B5',
          pale: '#E0F2F5',
        },
        sage: '#7A9E7E',
        amber: '#D4A574',
        coral: '#D97D54',
        error: '#C53030',
        success: '#38A169',
        warning: '#D69E2E',
      },
    },
  },
};
