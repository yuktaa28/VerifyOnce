import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <div className="card p-8 text-center">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p className="mt-2 text-muted">That address is not part of the VerifyOnce demo.</p>
      <Link to="/" className="btn btn-primary mt-5">
        Go to the home page
      </Link>
    </div>
  );
}
