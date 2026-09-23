export default function Footer({ note = 'Open any package and send an inquiry. Our team follows up by phone or email.' }) {
  return (
    <footer className="footer">
      <div className="container footer-inner">
        <img src="/static/img/logo-wordmark.png" alt="Heaven & Angel Scents" />
        <div className="footer-notes">
          <p>Curated fragrance bundles for distributors and resellers, priced below our regular list.</p>
          <p>{note}</p>
          <p>This link is shared privately with our distributors and resellers. Please don&apos;t forward it publicly.</p>
        </div>
        <div className="footer-legal">© {new Date().getFullYear()} Heaven &amp; Angel Scents</div>
      </div>
    </footer>
  );
}
