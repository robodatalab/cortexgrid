import cortexgrid

from models import (
    FAMILY,
    SQUARE_ROOTED_SUFFIX,
    ServedSquareRooted,
    SquareRooted,
)

XS = [0.0, 1.0, 2.0, 3.0]

def main() -> None:
    cortexgrid.Experiment.init("Examples-ModelDependencies")
    cortexgrid.register_model(
        SquareRooted,
        family=FAMILY,
        suffix=SQUARE_ROOTED_SUFFIX,
        requirements=SquareRooted.requirements(),
    )
    square_rooted = cortexgrid.deploy_model(FAMILY, SQUARE_ROOTED_SUFFIX, cortexgrid.IMPORTED)
    try:
        square_rooted_model: ServedSquareRooted = square_rooted.client()
        predicted = square_rooted_model.predict(XS)
        print(f"square rooted: {predicted}")
    finally:
        cortexgrid.undeploy_model(square_rooted.key)
        cortexgrid.delete_model(FAMILY, SQUARE_ROOTED_SUFFIX, cortexgrid.IMPORTED)


if __name__ == "__main__":
    main()
