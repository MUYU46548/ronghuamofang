import sys; sys.path.insert(0, 'scripts')
import chapter_review
import batch_refine
print('chapter_review + batch_refine import OK')
print('chapter_review.run_review:', callable(chapter_review.run_review))
print('batch_refine.run_batch_refine:', callable(batch_refine.run_batch_refine))
