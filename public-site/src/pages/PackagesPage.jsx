import { useEffect, useRef, useState } from 'react';
import { useLocation, useParams, useSearchParams } from 'react-router';
import { fetchPackages } from '../api.js';
import Footer from '../components/Footer.jsx';
import About from '../components/landing/About.jsx';
import BackdropVideo from '../components/landing/BackdropVideo.jsx';
import Collection from '../components/landing/Collection.jsx';
import CraftFilm from '../components/landing/CraftFilm.jsx';
import Faq from '../components/landing/Faq.jsx';
import Hero from '../components/landing/Hero.jsx';
import Nav from '../components/landing/Nav.jsx';
import PackagesSection from '../components/landing/PackagesSection.jsx';
import ScentJourney from '../components/landing/ScentJourney.jsx';
import Steps from '../components/landing/Steps.jsx';
import { useToast } from '../components/Toasts.jsx';

// Sections that show the fixed backdrop clip through a transparent
// background (.section-video). About and the film sit back to back, so
// they share it. The collection has its own interactive dot grid
// instead (DotField, in Collection.jsx).
const ABOUT_BACKDROP = ['about', 'craft'];

export default function PackagesPage() {
  const { slug } = useParams();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const scope = searchParams.get('scope') || 'all';
  const showToast = useToast();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    document.title = 'Business Partners · Heaven & Angel Scents';
  }, []);

  // The previous results stay on screen while a new filter loads, then
  // the grid re-flows into the new set (see PackagesSection).
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetchPackages(slug, scope, controller.signal)
      .then((result) => {
        setData(result);
        setLoading(false);
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        showToast(err.message, 'error');
        setData((prev) => prev || { packages: [], partner_types: ['Distributor', 'Reseller'], scope });
        setLoading(false);
      });
    return () => controller.abort();
  }, [slug, scope, showToast]);

  // A #section in the URL on first load (e.g. a shared ".../packages#faq"
  // link) can only be honored once the content it points at exists.
  const scrolledToHash = useRef(false);
  useEffect(() => {
    if (!data || scrolledToHash.current || !location.hash) return;
    scrolledToHash.current = true;
    document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [data, location.hash]);

  const changeScope = (next) => {
    if (next === scope) return;
    setSearchParams(next === 'all' ? {} : { scope: next }, { preventScrollReset: true, replace: true });
  };

  return (
    <div className="page">
      <BackdropVideo src="/static/video/black_background.mp4" sectionIds={ABOUT_BACKDROP} invert={false} speed={0.7} />
      <Nav />
      <main>
        <Hero />
        {/* The packages are what partners come for, so they follow the
            hero directly; the brand story comes after. */}
        <PackagesSection slug={slug} data={data} loading={loading} scope={scope} onScopeChange={changeScope} />
        <About />
        <CraftFilm />
        <Collection />
        <ScentJourney />
        <Steps />
        <Faq />
      </main>
      <Footer />
    </div>
  );
}
