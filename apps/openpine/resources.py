"""Resource flags shared by the two application transports."""

from packages.contracts.storage import ResourcePolicy, CoordinatorPolicy


def arguments(parser):
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--threads-per-worker", type=int, default=1)
    parser.add_argument("--worker-memory-mb", type=int, default=512)
    parser.add_argument("--total-memory-mb", type=int, default=1024)
    parser.add_argument("--max-attempts", type=int, default=1, help="1..3; retry infrastructure failures only")
    parser.add_argument("--coordinator-memory-mb", type=int, default=512,
        help="coordinator budget in addition to the worker pool --total-memory-mb")


def policy(args):
    return ResourcePolicy(workers=args.workers, threads_per_worker=args.threads_per_worker,
        memory_mb_per_worker=args.worker_memory_mb, total_memory_mb=args.total_memory_mb,
        max_attempts=args.max_attempts)


def coordinator_policy(args):
    return CoordinatorPolicy(memory_mb=args.coordinator_memory_mb)
