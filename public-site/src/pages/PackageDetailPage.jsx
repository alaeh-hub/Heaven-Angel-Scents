import { Link, useNavigate, useParams } from 'react-router';
import { ArrowLeftIcon } from '@phosphor-icons/react';
import Footer from '../components/Footer.jsx';
import PackageDetail from '../components/PackageDetail.jsx';
import { packagesPath } from '../utils.js';

/** A package opened directly by its URL (bookmark, shared link, new tab). */
export default function PackageDetailPage() {
  const { slug, packageId } = useParams();
  const navigate = useNavigate();
  const listUrl = packagesPath(slug);

  return (
    <div className="page">
      <header className="detail-topbar">
        <div className="container">
          <Link to={listUrl} aria-label="Heaven & Angel Scents, all packages">
            <img src="/static/img/logo-wordmark.png" alt="" />
          </Link>
          <Link to={listUrl} className="detail-back" style={{ margin: 0 }}>
            <ArrowLeftIcon size={16} weight="bold" /> All packages
          </Link>
        </div>
      </header>
      <main>
        <PackageDetail
          slug={slug}
          packageId={packageId}
          onMissing={() => navigate(listUrl, { replace: true })}
        />
      </main>
      <Footer note="Send an inquiry from this page. It's the fastest way to reach our team." />
    </div>
  );
}
