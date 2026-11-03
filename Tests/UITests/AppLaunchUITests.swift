import XCTest

final class AppLaunchUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func test_launch_showsNavigationBar() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.navigationBars.firstMatch.waitForExistence(timeout: 10))
    }
}
