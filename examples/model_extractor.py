import sys
from datetime import datetime

from scdatatools.sc.utils import extract_ship


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser('SC ship extractor')
    parser.add_argument('sc_dir', help='Star Citizen Directory')
    parser.add_argument('ship', help='Ship entity to extract. Either a datacore path, or GUID (e.g. ORIG_100i)')
    parser.add_argument('output_dir', help='Output directory to extract data into')
    parser.add_argument('-r', '--remove-outdir', action='store_true',
                        help='Remove and recreate `outdir` before extracting')
    args = parser.parse_args()

    start = datetime.now()
    try:
        extract_ship(args.sc_dir, args.ship, args.output_dir, remove_outdir=args.remove_outdir)
        print(f'Finished in {datetime.now() - start}')
    except Exception as e:
        sys.stderr.write(str(e) + '\n')
        sys.exit(1)
