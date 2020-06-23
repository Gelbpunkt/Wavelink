for f in wavelink/*.py; do
    STUFF=$(strip-hints $f)
    echo "$STUFF" > $f
done
