import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { MotionConfig } from 'motion/react';
import App from './App.jsx';
import './styles/tokens.css';
import './styles/landing.css';
import './styles/detail.css';
import './styles/catalog.css';

// On a reload the browser's own scroll restoration jumps back to the old
// scroll position before the videos/marquee/images have re-established
// their final size, landing mid-reflow (which some mobile browsers show
// as the page zoomed out until it settles). Start at the top instead.
if ('scrollRestoration' in window.history) {
  window.history.scrollRestoration = 'manual';
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* reducedMotion="user": for visitors who ask their OS for less motion,
        every Motion animation keeps its fade but drops the movement. */}
    <MotionConfig reducedMotion="user">
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </MotionConfig>
  </StrictMode>,
);
