"""
JSON documents corresponding to packages is kept in a single collection.

'releases' are dropped to prevent '.'-keyed fields from causing issues on
storing in Mongo.
"""


# For accessing all wheel files.
wheelfile_pipeline = [
    {
        "$match": {
            "urls.filename": {
                "$regex": "\\.whl$"
            }
        }
    }, {
        "$unwind": {
            "path": "$urls",
            "preserveNullAndEmptyArrays": False
        }
    }, {
        "$project": {
            "urls": 1
        }
    }, {
        '$addFields': {
            'urls._id': '$_id'
        }
    }, {
        "$replaceRoot": {
            "newRoot": "$urls"
        }
    }, {
        "$match": {
            "filename": {
                "$regex": "\\.whl$"
            }
        }
    }
]
